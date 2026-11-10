"""The change of password is also limited per client address (#88, RNF25, ADR 0007)."""

import pytest
from django.conf import settings
from django.test import override_settings

from tests.api.helpers import TOO_MANY_REQUESTS, bearer, post_json, travel
from tests.factories import DEFAULT_PASSWORD, make_user

pytestmark = pytest.mark.django_db

CHANGE_URL = "/api/auth/change-password"
WEAK_PASSWORD = "123"


def limit_and_window():
    return settings.AUTH_RATE_LIMITS["change_password_ip"]


def weak_change(browser, user, **extra):
    """A request that costs the address one hit but never counts as a wrong current password."""
    body = {
        "current_password": DEFAULT_PASSWORD,
        "password": WEAK_PASSWORD,
        "password_confirmation": WEAK_PASSWORD,
    }
    return post_json(browser, CHANGE_URL, body, headers=bearer(user), **extra)


def test_the_scope_has_its_own_default_limit_and_window():
    limit, window = limit_and_window()

    assert (limit, window) == (10, 60 * 60)


def test_after_the_limit_the_endpoint_answers_429_with_retry_after(browser):
    user = make_user()
    limit, window = limit_and_window()

    statuses = [weak_change(browser, user).status_code for _ in range(limit)]
    blocked = weak_change(browser, user)

    assert 429 not in statuses
    assert blocked.status_code == 429
    assert blocked.json() == TOO_MANY_REQUESTS
    assert 0 < int(blocked["Retry-After"]) <= window


def test_the_limit_is_per_address_whatever_the_account(browser):
    limit, _ = limit_and_window()
    users = [make_user() for _ in range(limit + 1)]

    for user in users[:limit]:
        assert weak_change(browser, user).status_code != 429
    blocked = weak_change(browser, users[limit])

    assert blocked.status_code == 429


@override_settings(NUM_PROXIES=1)
def test_another_client_address_is_not_blocked(browser):
    user = make_user()
    limit, _ = limit_and_window()
    noisy = {"HTTP_X_FORWARDED_FOR": "203.0.113.7"}
    for _ in range(limit + 1):
        weak_change(browser, user, **noisy)

    blocked = weak_change(browser, user, **noisy)
    other = weak_change(browser, user, HTTP_X_FORWARDED_FOR="198.51.100.20")

    assert blocked.status_code == 429
    assert other.status_code != 429


def test_the_window_ends_and_the_address_can_try_again(browser):
    user = make_user()
    limit, window = limit_and_window()
    for _ in range(limit + 1):
        weak_change(browser, user)
    assert weak_change(browser, user).status_code == 429

    with travel(seconds=window + 1):
        assert weak_change(browser, user).status_code != 429


def test_the_address_count_comes_before_the_origin_check(client):
    """A request refused for its origin still uses the quota of its address (ADR 0007)."""
    user = make_user()
    limit, _ = limit_and_window()
    body = {
        "current_password": DEFAULT_PASSWORD,
        "password": WEAK_PASSWORD,
        "password_confirmation": WEAK_PASSWORD,
    }

    statuses = [
        post_json(client, CHANGE_URL, body, headers=bearer(user)).status_code
        for _ in range(limit + 1)
    ]

    assert statuses[:limit] == [403] * limit
    assert statuses[limit] == 429


def test_the_endpoint_does_not_share_its_counter_with_the_other_scopes(browser):
    user = make_user()
    limit, _ = limit_and_window()
    for _ in range(limit + 1):
        weak_change(browser, user)

    assert weak_change(browser, user).status_code == 429
    assert post_json(browser, "/api/auth/logout", {}).status_code == 204


def test_the_limit_and_the_window_come_from_the_environment_names_documented_in_the_example():
    example = (settings.BASE_DIR / ".env.example").read_text()

    assert "AUTH_CHANGE_PASSWORD_IP_LIMIT=10" in example
    assert "AUTH_CHANGE_PASSWORD_IP_WINDOW_SECONDS=3600" in example
