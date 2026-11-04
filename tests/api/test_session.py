from datetime import timedelta

import pytest
from django.test import Client
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


def sign_in(browser, email="ana@example.com"):
    user = make_user(email=email)
    response = post_json(browser, LOGIN_URL, {"email": email, "password": DEFAULT_PASSWORD})
    assert response.status_code == 200
    return user, response.json()["access_token"]


def refresh(browser, **headers):
    return browser.post(REFRESH_URL, **headers)


def test_refresh_issues_a_new_access_token_and_rotates_the_cookie(browser):
    sign_in(browser)
    first_cookie = browser.cookies[COOKIE].value

    response = refresh(browser, HTTP_ORIGIN=TRUSTED_ORIGIN)

    assert response.status_code == 200
    new_token = response.json()["access_token"]
    assert browser.get(ME_URL, headers={"Authorization": f"Bearer {new_token}"}).status_code == 200
    assert response.cookies[COOKIE].value != first_cookie
    assert response.cookies[COOKIE]["httponly"] is True


def test_a_used_refresh_token_cannot_be_replayed(browser):
    sign_in(browser)
    used_cookie = browser.cookies[COOKIE].value
    assert refresh(browser).status_code == 200

    browser.cookies[COOKIE] = used_cookie

    assert refresh(browser).status_code == 401


def test_refresh_without_a_valid_cookie_is_401(browser):
    assert refresh(browser).status_code == 401

    browser.cookies[COOKIE] = "isto-nao-e-um-token"

    assert refresh(browser).status_code == 401


def test_an_expired_refresh_token_is_rejected(browser):
    user = make_user()
    expired = RefreshToken.for_user(user)
    expired.set_exp(lifetime=-timedelta(seconds=1))
    browser.cookies[COOKIE] = str(expired)

    assert refresh(browser).status_code == 401


def test_a_signed_token_that_does_not_identify_a_user_is_rejected(browser):
    browser.cookies[COOKIE] = str(RefreshToken())

    assert refresh(browser).status_code == 401


def test_refresh_is_denied_once_the_account_is_deactivated(browser):
    user, _ = sign_in(browser)
    user.is_active = False
    user.save()

    assert refresh(browser).status_code == 401


def test_refresh_from_an_untrusted_origin_is_forbidden_and_keeps_the_session_untouched(browser):
    sign_in(browser)

    response = refresh(browser, HTTP_ORIGIN=UNTRUSTED_ORIGIN)

    assert response.status_code == 403
    assert COOKIE not in response.cookies
    assert refresh(browser, HTTP_ORIGIN=TRUSTED_ORIGIN).status_code == 200


def test_refresh_only_accepts_post(browser):
    assert browser.get(REFRESH_URL).status_code == 405


def test_logout_blocks_the_refresh_token_and_clears_the_cookie(browser):
    sign_in(browser)
    cookie = browser.cookies[COOKIE].value

    response = browser.post(LOGOUT_URL, HTTP_ORIGIN=TRUSTED_ORIGIN)

    assert response.status_code == 204
    assert response.cookies[COOKIE]["max-age"] == 0
    browser.cookies[COOKIE] = cookie
    assert refresh(browser).status_code == 401


def test_logout_without_a_session_still_succeeds(browser):
    assert browser.post(LOGOUT_URL).status_code == 204


def test_logout_from_an_untrusted_origin_is_forbidden(browser):
    sign_in(browser)

    assert browser.post(LOGOUT_URL, HTTP_ORIGIN=UNTRUSTED_ORIGIN).status_code == 403
    assert refresh(browser).status_code == 200


def test_current_user_requires_a_valid_access_token(browser):
    assert browser.get(ME_URL).status_code == 401
    assert browser.get(ME_URL, headers={"Authorization": "Bearer invalido"}).status_code == 401


def test_access_token_stops_working_when_the_account_is_deactivated(browser):
    user, access_token = sign_in(browser)
    user.is_active = False
    user.save()

    response = browser.get(ME_URL, headers={"Authorization": f"Bearer {access_token}"})

    assert response.status_code == 401


def log_in(client, email):
    response = post_json(client, LOGIN_URL, {"email": email, "password": DEFAULT_PASSWORD})
    assert response.status_code == 200


def test_replaying_a_used_refresh_token_revokes_every_session_of_that_user(browser):
    user = make_user(email="ana@example.com")
    phone = Client(HTTP_ORIGIN=TRUSTED_ORIGIN)
    log_in(browser, user.email)
    log_in(phone, user.email)
    stolen = browser.cookies[COOKIE].value
    assert refresh(browser).status_code == 200
    newest = browser.cookies[COOKIE].value

    browser.cookies[COOKIE] = stolen
    assert refresh(browser).status_code == 401

    assert refresh(phone).status_code == 401
    browser.cookies[COOKIE] = newest
    assert refresh(browser).status_code == 401


def test_replaying_a_token_does_not_affect_the_sessions_of_other_users(browser):
    victim = make_user(email="ana@example.com")
    bystander = make_user(email="bruno@example.com")
    other = Client(HTTP_ORIGIN=TRUSTED_ORIGIN)
    log_in(browser, victim.email)
    log_in(other, bystander.email)
    stolen = browser.cookies[COOKIE].value
    assert refresh(browser).status_code == 200

    browser.cookies[COOKIE] = stolen
    assert refresh(browser).status_code == 401

    assert refresh(other).status_code == 200
