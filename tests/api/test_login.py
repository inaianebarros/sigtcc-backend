import pytest
from django.test import override_settings

from tests.api.helpers import post_json
from tests.factories import DEFAULT_PASSWORD, make_user

pytestmark = pytest.mark.django_db

LOGIN_URL = "/api/auth/login"
ME_URL = "/api/auth/me"
REFRESH_COOKIE = "refresh_token"


def login(client, email, password=DEFAULT_PASSWORD):
    return post_json(client, LOGIN_URL, {"email": email, "password": password})


def test_valid_credentials_return_an_access_token_that_identifies_the_user(client):
    user = make_user(email="ana@example.com")

    response = login(client, "ana@example.com")

    assert response.status_code == 200
    body = response.json()
    assert body["user"] == {
        "id": user.id,
        "full_name": user.full_name,
        "email": "ana@example.com",
        "type": "student",
    }
    me = client.get(ME_URL, headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == "ana@example.com"


def test_login_sets_the_refresh_token_in_a_restricted_http_only_cookie(client):
    make_user(email="ana@example.com")

    cookie = login(client, "ana@example.com").cookies[REFRESH_COOKIE]

    assert cookie.value
    assert cookie["httponly"] is True
    assert cookie["samesite"] == "Lax"
    assert cookie["path"] == "/api/auth"
    assert cookie["max-age"] == 30 * 60


@override_settings(JWT_REFRESH_COOKIE_SECURE=True)
def test_refresh_cookie_is_secure_when_configured(client):
    make_user(email="ana@example.com")

    cookie = login(client, "ana@example.com").cookies[REFRESH_COOKIE]

    assert cookie["secure"] is True


def test_login_ignores_the_case_of_the_email(client):
    make_user(email="Ana.Souza@Example.com")

    assert login(client, "ana.souza@example.com").status_code == 200


def test_wrong_password_unknown_email_and_inactive_account_get_the_same_generic_401(client):
    make_user(email="ana@example.com")
    inactive = make_user(email="inativo@example.com")
    inactive.is_active = False
    inactive.save()

    responses = [
        login(client, "ana@example.com", "senha-errada-123"),
        login(client, "ninguem@example.com"),
        login(client, "inativo@example.com"),
    ]

    assert {response.status_code for response in responses} == {401}
    assert len({response.content for response in responses}) == 1
    assert all(REFRESH_COOKIE not in response.cookies for response in responses)


def test_login_requires_email_and_password(client):
    response = post_json(client, LOGIN_URL, {"email": "ana@example.com"})

    assert response.status_code == 422
