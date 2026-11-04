import re
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.test import Client

from apps.accounts.models import User
from tests.api.conftest import TRUSTED_ORIGIN
from tests.api.helpers import LOGIN_URL, REFRESH_COOKIE, REFRESH_URL, errors_by_field, post_json
from tests.factories import DEFAULT_PASSWORD, make_user

pytestmark = pytest.mark.django_db

FORGOT_URL = "/api/auth/forgot-password"
RESET_URL = "/api/auth/reset-password"
NEW_PASSWORD = "Nova-Senha-Segura-2026"
GENERIC_RESPONSE = {
    "detail": "Se o e-mail estiver cadastrado, enviaremos um link para redefinir a senha."
}


@contextmanager
def minutes_from_now(minutes):
    later = datetime.now() + timedelta(minutes=minutes)
    with patch.object(PasswordResetTokenGenerator, "_now", return_value=later):
        yield


def request_reset(client, email):
    return post_json(client, FORGOT_URL, {"email": email})


def link_parts(mailoutbox):
    body = mailoutbox[-1].body
    match = re.search(r"uid=([^&\s]+)&token=([^\s]+)", body)
    return match.group(1), match.group(2)


def reset(client, uid, token, password=NEW_PASSWORD, confirmation=None):
    return post_json(
        client,
        RESET_URL,
        {
            "uid": uid,
            "token": token,
            "password": password,
            "password_confirmation": confirmation if confirmation is not None else password,
        },
    )


def log_in(password):
    browser = Client(HTTP_ORIGIN=TRUSTED_ORIGIN)
    return post_json(browser, LOGIN_URL, {"email": "ana@example.com", "password": password})


def issued_link(client, mailoutbox, email="ana@example.com"):
    make_user(email=email)
    request_reset(client, email)
    return link_parts(mailoutbox)


def test_a_registered_email_receives_a_reset_link_to_the_frontend(client, mailoutbox):
    make_user(email="ana@example.com")

    response = request_reset(client, "ana@example.com")

    assert response.status_code == 200
    assert response.json() == GENERIC_RESPONSE
    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == ["ana@example.com"]
    assert "http://localhost:3000/redefinir-senha?uid=" in mailoutbox[0].body
    assert "1 hora" in mailoutbox[0].body


def test_the_email_lookup_ignores_case(client, mailoutbox):
    make_user(email="ana.souza@example.com")

    request_reset(client, "ANA.Souza@Example.com")

    assert len(mailoutbox) == 1


def test_unknown_and_inactive_accounts_get_the_same_response_and_no_email(client, mailoutbox):
    inactive = make_user(email="inativo@example.com")
    inactive.is_active = False
    inactive.save()

    unknown = request_reset(client, "ninguem@example.com")
    deactivated = request_reset(client, "inativo@example.com")

    assert unknown.json() == deactivated.json() == GENERIC_RESPONSE
    assert unknown.status_code == deactivated.status_code == 200
    assert mailoutbox == []


def test_the_request_requires_an_email_field(client):
    assert post_json(client, FORGOT_URL, {}).status_code == 422


def test_the_link_sets_a_new_password_and_the_old_one_stops_working(client, mailoutbox):
    uid, token = issued_link(client, mailoutbox)

    response = reset(client, uid, token)

    assert response.status_code == 200
    assert log_in(NEW_PASSWORD).status_code == 200
    assert log_in(DEFAULT_PASSWORD).status_code == 401


def test_a_link_can_be_used_only_once(client, mailoutbox):
    uid, token = issued_link(client, mailoutbox)
    assert reset(client, uid, token).status_code == 200

    assert reset(client, uid, token, password="Outra-Senha-Forte-2026").status_code == 400


def test_the_link_is_valid_for_just_under_an_hour(client, mailoutbox):
    uid, token = issued_link(client, mailoutbox)

    with minutes_from_now(59):
        assert reset(client, uid, token).status_code == 200


def test_the_link_expires_after_one_hour(client, mailoutbox):
    uid, token = issued_link(client, mailoutbox)

    with minutes_from_now(61):
        assert reset(client, uid, token).status_code == 400


@pytest.mark.parametrize("tamper", ["token", "uid"])
def test_a_tampered_link_is_rejected(client, mailoutbox, tamper):
    uid, token = issued_link(client, mailoutbox)
    if tamper == "token":
        token = token[:-1] + ("a" if token[-1] != "a" else "b")
    else:
        uid = "MTAwMDAw"

    assert reset(client, uid, token).status_code == 400


def test_a_deactivated_account_cannot_reset_its_password(client, mailoutbox):
    uid, token = issued_link(client, mailoutbox)
    User.objects.filter(email="ana@example.com").update(is_active=False)

    assert reset(client, uid, token).status_code == 400


def test_the_new_password_must_match_its_confirmation(client, mailoutbox):
    uid, token = issued_link(client, mailoutbox)

    response = reset(client, uid, token, confirmation="Diferente-Senha-2026")

    assert response.status_code == 422
    assert "password_confirmation" in errors_by_field(response)


def test_a_weak_password_is_rejected_and_the_link_stays_usable(client, mailoutbox):
    uid, token = issued_link(client, mailoutbox)

    weak = reset(client, uid, token, password="password")

    assert weak.status_code == 422
    assert "password" in errors_by_field(weak)
    assert reset(client, uid, token).status_code == 200


def test_resetting_the_password_ends_every_active_session(client, mailoutbox):
    uid, token = issued_link(client, mailoutbox)
    browser = Client(HTTP_ORIGIN=TRUSTED_ORIGIN)
    post_json(browser, LOGIN_URL, {"email": "ana@example.com", "password": DEFAULT_PASSWORD})
    assert browser.cookies[REFRESH_COOKIE].value

    assert reset(client, uid, token).status_code == 200

    assert browser.post(REFRESH_URL).status_code == 401
