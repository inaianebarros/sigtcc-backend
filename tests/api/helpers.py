import time
from contextlib import contextmanager
from unittest.mock import patch

from django.contrib.auth import get_user_model
from ninja_jwt.tokens import RefreshToken

PASSWORD = "Senha-Segura-2026"

LOGIN_URL = "/api/auth/login"
REFRESH_URL = "/api/auth/refresh"
LOGOUT_URL = "/api/auth/logout"
ME_URL = "/api/auth/me"
REFRESH_COOKIE = "refresh_token"


def post_json(client, url, payload, **extra):
    return client.post(url, data=payload, content_type="application/json", **extra)


def errors_by_field(response):
    return {error["loc"][-1]: error["msg"] for error in response.json()["detail"]}


def assert_rejected_on(client, url, payload, field):
    users_before = get_user_model().objects.count()

    response = post_json(client, url, payload)

    assert response.status_code == 422
    assert field in errors_by_field(response)
    assert get_user_model().objects.count() == users_before


def bearer(user):
    return {"Authorization": f"Bearer {RefreshToken.for_user(user).access_token}"}


TOO_MANY_REQUESTS = {"detail": "Muitas tentativas. Aguarde antes de tentar novamente."}


@contextmanager
def travel(seconds):
    """Move the clock seen by the cache and the rate-limit counters forward."""
    real_time = time.time
    with patch("time.time", side_effect=lambda: real_time() + seconds):
        yield
