import logging
from unittest.mock import patch

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.settings import _auth_rate
from tests.api.helpers import LOGIN_URL, post_json
from tests.factories import DEFAULT_PASSWORD, make_user

pytestmark = pytest.mark.django_db


def test_a_broken_cache_does_not_take_the_login_down_and_is_logged(browser, caplog):
    make_user(email="ana@example.com")

    with patch("django.core.cache.cache.add", side_effect=ConnectionError("cache fora do ar")):
        response = post_json(
            browser, LOGIN_URL, {"email": "ana@example.com", "password": DEFAULT_PASSWORD}
        )

    assert response.status_code == 200
    assert any(
        record.levelno >= logging.ERROR and "rate limit" in record.getMessage()
        for record in caplog.records
    )


def test_a_window_that_expires_between_the_two_cache_calls_is_counted_again(browser):
    from config import rate_limit

    expired_once = iter([ValueError("expirou"), 2])

    def incr(key):
        outcome = next(expired_once)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    rate_limit.hit("rl:test:window", 60)
    with patch("django.core.cache.cache.incr", side_effect=incr):
        count, _ = rate_limit.hit("rl:test:window", 60)

    assert count >= 1


@pytest.mark.parametrize(
    ("name", "value"),
    [("LIMIT", "0"), ("LIMIT", "-3"), ("WINDOW_SECONDS", "0"), ("WINDOW_SECONDS", "-60")],
)
def test_a_limit_or_window_below_one_is_rejected_at_startup(monkeypatch, name, value):
    monkeypatch.setenv(f"AUTH_LOGIN_IP_{name}", value)

    with pytest.raises(ImproperlyConfigured):
        _auth_rate("LOGIN_IP", 20, 60)


def test_valid_values_from_the_environment_are_used(monkeypatch):
    monkeypatch.setenv("AUTH_LOGIN_IP_LIMIT", "7")
    monkeypatch.setenv("AUTH_LOGIN_IP_WINDOW_SECONDS", "120")

    assert _auth_rate("LOGIN_IP", 20, 60) == (7, 120)
