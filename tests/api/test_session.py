from datetime import timedelta

import pytest
from ninja_jwt.tokens import RefreshToken

from tests.api.helpers import (
    LOGIN_URL,
    LOGOUT_URL,
    ME_URL,
    REFRESH_URL,
    post_json,
)
from tests.api.helpers import (
    REFRESH_COOKIE as COOKIE,
)
from tests.factories import DEFAULT_PASSWORD, make_user

pytestmark = pytest.mark.django_db

TRUSTED_ORIGIN = "http://localhost:3000"
UNTRUSTED_ORIGIN = "https://evil.example"


def sign_in(client, email="ana@example.com"):
    user = make_user(email=email)
    response = post_json(client, LOGIN_URL, {"email": email, "password": DEFAULT_PASSWORD})
    assert response.status_code == 200
    return user, response.json()["access_token"]


def refresh(client, **headers):
    return client.post(REFRESH_URL, **headers)


def test_refresh_issues_a_new_access_token_and_rotates_the_cookie(client):
    sign_in(client)
    first_cookie = client.cookies[COOKIE].value

    response = refresh(client, HTTP_ORIGIN=TRUSTED_ORIGIN)

    assert response.status_code == 200
    new_token = response.json()["access_token"]
    assert client.get(ME_URL, headers={"Authorization": f"Bearer {new_token}"}).status_code == 200
    assert response.cookies[COOKIE].value != first_cookie
    assert response.cookies[COOKIE]["httponly"] is True


def test_a_used_refresh_token_cannot_be_replayed(client):
    sign_in(client)
    used_cookie = client.cookies[COOKIE].value
    assert refresh(client).status_code == 200

    client.cookies[COOKIE] = used_cookie

    assert refresh(client).status_code == 401


def test_refresh_without_a_valid_cookie_is_401(client):
    assert refresh(client).status_code == 401

    client.cookies[COOKIE] = "isto-nao-e-um-token"

    assert refresh(client).status_code == 401


def test_an_expired_refresh_token_is_rejected(client):
    user = make_user()
    expired = RefreshToken.for_user(user)
    expired.set_exp(lifetime=-timedelta(seconds=1))
    client.cookies[COOKIE] = str(expired)

    assert refresh(client).status_code == 401


def test_a_signed_token_that_does_not_identify_a_user_is_rejected(client):
    client.cookies[COOKIE] = str(RefreshToken())

    assert refresh(client).status_code == 401


def test_refresh_is_denied_once_the_account_is_deactivated(client):
    user, _ = sign_in(client)
    user.is_active = False
    user.save()

    assert refresh(client).status_code == 401


def test_refresh_from_an_untrusted_origin_is_forbidden_and_keeps_the_session_untouched(client):
    sign_in(client)

    response = refresh(client, HTTP_ORIGIN=UNTRUSTED_ORIGIN)

    assert response.status_code == 403
    assert COOKIE not in response.cookies
    assert refresh(client, HTTP_ORIGIN=TRUSTED_ORIGIN).status_code == 200


def test_refresh_only_accepts_post(client):
    assert client.get(REFRESH_URL).status_code == 405


def test_logout_blocks_the_refresh_token_and_clears_the_cookie(client):
    sign_in(client)
    cookie = client.cookies[COOKIE].value

    response = client.post(LOGOUT_URL, HTTP_ORIGIN=TRUSTED_ORIGIN)

    assert response.status_code == 204
    assert response.cookies[COOKIE]["max-age"] == 0
    client.cookies[COOKIE] = cookie
    assert refresh(client).status_code == 401


def test_logout_without_a_session_still_succeeds(client):
    assert client.post(LOGOUT_URL).status_code == 204


def test_logout_from_an_untrusted_origin_is_forbidden(client):
    sign_in(client)

    assert client.post(LOGOUT_URL, HTTP_ORIGIN=UNTRUSTED_ORIGIN).status_code == 403
    assert refresh(client).status_code == 200


def test_current_user_requires_a_valid_access_token(client):
    assert client.get(ME_URL).status_code == 401
    assert client.get(ME_URL, headers={"Authorization": "Bearer invalido"}).status_code == 401


def test_access_token_stops_working_when_the_account_is_deactivated(client):
    user, access_token = sign_in(client)
    user.is_active = False
    user.save()

    response = client.get(ME_URL, headers={"Authorization": f"Bearer {access_token}"})

    assert response.status_code == 401
