import time
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from django.conf import settings
from django.test import override_settings

from tests.api.helpers import LOGOUT_URL, REFRESH_URL, post_json

pytestmark = pytest.mark.django_db

FORGOT_URL = "/api/auth/forgot-password"
RESET_URL = "/api/auth/reset-password"


@contextmanager
def travel(seconds):
    real_time = time.time
    with patch("time.time", side_effect=lambda: real_time() + seconds):
        yield


TOO_MANY = {"detail": "Muitas tentativas. Aguarde antes de tentar novamente."}

RESET_BODY = {
    "uid": "x",
    "token": "y",
    "password": "Nova-Senha-2026",
    "password_confirmation": "Nova-Senha-2026",
}


def send(browser, url, body=None, **extra):
    return post_json(browser, url, body or {}, **extra)


@pytest.mark.parametrize(
    ("url", "body", "limit"),
    [
        (REFRESH_URL, {}, 30),
        (LOGOUT_URL, {}, 30),
        (RESET_URL, RESET_BODY, 10),
        (FORGOT_URL, {"email": "alguem@example.com"}, 5),
    ],
)
def test_each_endpoint_is_limited_per_address_with_the_standard_429(browser, url, body, limit):
    statuses = [send(browser, url, body).status_code for _ in range(limit)]
    blocked = send(browser, url, body)

    assert 429 not in statuses
    assert blocked.status_code == 429
    assert blocked.json() == TOO_MANY
    assert 0 < int(blocked["Retry-After"]) <= 60 * 60


@override_settings(NUM_PROXIES=1)
@pytest.mark.parametrize(
    ("url", "body", "limit"),
    [
        (REFRESH_URL, {}, 30),
        (RESET_URL, RESET_BODY, 10),
        (FORGOT_URL, {"email": "a@example.com"}, 5),
    ],
)
def test_the_limit_is_counted_per_client_address(browser, url, body, limit):
    noisy = {"HTTP_X_FORWARDED_FOR": "203.0.113.7"}
    for _ in range(limit + 1):
        send(browser, url, body, **noisy)

    blocked = send(browser, url, body, **noisy)
    other = send(browser, url, body, HTTP_X_FORWARDED_FOR="198.51.100.20")

    assert blocked.status_code == 429
    assert other.status_code != 429


def test_the_endpoints_do_not_share_one_counter(browser):
    for _ in range(31):
        send(browser, REFRESH_URL)

    assert send(browser, REFRESH_URL).status_code == 429
    assert send(browser, LOGOUT_URL).status_code == 204
    assert send(browser, FORGOT_URL, {"email": "a@example.com"}).status_code == 200


def test_the_window_of_an_endpoint_ends(browser):
    for _ in range(31):
        send(browser, FORGOT_URL, {"email": "a@example.com"})
    assert send(browser, FORGOT_URL, {"email": "a@example.com"}).status_code == 429

    with travel(seconds=60 * 60 + 1):
        assert send(browser, FORGOT_URL, {"email": "a@example.com"}).status_code == 200


@override_settings(AUTH_RATE_LIMITS={**settings.AUTH_RATE_LIMITS, "refresh_ip": (2, 10)})
def test_limits_and_windows_come_from_the_settings(browser):
    statuses = [send(browser, REFRESH_URL).status_code for _ in range(3)]

    assert statuses[:2] != [429, 429]
    assert statuses[2] == 429


@pytest.mark.parametrize(
    "path",
    [
        "/api/auth/login",
        "/api/auth/refresh",
        "/api/auth/logout",
        "/api/auth/forgot-password",
        "/api/auth/reset-password",
    ],
)
def test_the_openapi_documents_the_429_of_each_authentication_endpoint(client, path):
    schema = client.get("/api/openapi.json").json()

    responses = schema["paths"][path]["post"]["responses"]
    assert "429" in responses
