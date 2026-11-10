"""A refresh racing with the revocation of the sessions leaves none alive (#88, RN27, ADR 0006).

Real threads and transactions against PostgreSQL. The revocation (a password reset, a password
change, a deactivation) and the rotation of a refresh token both take the user's row lock, so
whichever comes second sees what the first one did.
"""

import pytest
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from ninja_jwt.token_blacklist.models import OutstandingToken
from ninja_jwt.tokens import RefreshToken

from apps.accounts.deactivation import deactivate_user
from apps.accounts.models import User
from apps.accounts.password_reset import reset_password
from apps.accounts.sessions import revoke_user_sessions
from tests.api.helpers import (
    NEW_PASSWORD,
    REFRESH_COOKIE,
    REFRESH_URL,
    post_json,
    send_while_a_lock_is_held,
)
from tests.factories import make_user

pytestmark = pytest.mark.django_db(transaction=True)


def live_tokens(user):
    return OutstandingToken.objects.filter(user=user, blacklistedtoken__isnull=True).count()


def revoke(locked):
    revoke_user_sessions(locked.pk)


def reset(locked):
    uid = urlsafe_base64_encode(force_bytes(locked.pk))
    token = default_token_generator.make_token(locked)
    reset_password(uid, token, NEW_PASSWORD, NEW_PASSWORD)


def deactivate(locked):
    deactivate_user(locked)


@pytest.mark.parametrize("revocation", [revoke, reset, deactivate])
def test_a_refresh_waiting_for_the_revocation_is_refused_and_no_session_survives(
    browser, revocation
):
    user = make_user()
    refresh = RefreshToken.for_user(user)
    browser.cookies[REFRESH_COOKIE] = str(refresh)

    def revoke_while_holding_the_lock(wait_for_the_request):
        locked = User.objects.select_for_update().get(pk=user.pk)
        wait_for_the_request()
        revocation(locked)

    response = send_while_a_lock_is_held(
        revoke_while_holding_the_lock, lambda: post_json(browser, REFRESH_URL, {})
    )

    assert response.status_code == 401
    assert live_tokens(user) == 0


def test_a_revocation_waiting_for_a_rotation_also_revokes_the_token_the_rotation_created():
    user = make_user()
    RefreshToken.for_user(user)
    created_during_the_rotation = []

    def rotate_while_holding_the_lock(wait_for_the_request):
        locked = User.objects.select_for_update().get(pk=user.pk)
        wait_for_the_request()
        created_during_the_rotation.append(str(RefreshToken.for_user(locked)["jti"]))

    send_while_a_lock_is_held(rotate_while_holding_the_lock, lambda: revoke_user_sessions(user.pk))

    assert len(created_during_the_rotation) == 1
    assert OutstandingToken.objects.filter(user=user).count() == 2
    assert live_tokens(user) == 0


def test_a_refresh_that_wins_the_lock_still_ends_up_revoked_by_the_revocation(browser):
    """The other order: the rotation commits first, then the revocation takes everything."""
    user = make_user()
    browser.cookies[REFRESH_COOKIE] = str(RefreshToken.for_user(user))

    rotated = post_json(browser, REFRESH_URL, {})
    revoke_user_sessions(user.pk)

    assert rotated.status_code == 200
    assert live_tokens(user) == 0


def test_a_reset_link_used_at_the_same_time_works_only_once(browser):
    user = make_user()
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    body = {
        "uid": uid,
        "token": token,
        "password": "Outra-Senha-Segura-2026",
        "password_confirmation": "Outra-Senha-Segura-2026",
    }

    def reset_while_holding_the_lock(wait_for_the_request):
        User.objects.select_for_update().get(pk=user.pk)
        wait_for_the_request()
        reset_password(uid, token, NEW_PASSWORD, NEW_PASSWORD)

    second_use = send_while_a_lock_is_held(
        reset_while_holding_the_lock,
        lambda: post_json(browser, "/api/auth/reset-password", body),
    )

    assert second_use.status_code == 400
    assert User.objects.get(pk=user.pk).check_password(NEW_PASSWORD)
