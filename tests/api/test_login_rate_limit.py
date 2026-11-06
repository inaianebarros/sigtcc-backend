import time
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from django.test import override_settings

from tests.api.helpers import LOGIN_URL, post_json
from tests.factories import DEFAULT_PASSWORD, make_user

pytestmark = pytest.mark.django_db

FAILURE_LIMIT = 5


@contextmanager
def travel(seconds):
    """Avança o relógio visto pelo cache e pelos contadores."""
    real_time = time.time
    with patch("time.time", side_effect=lambda: real_time() + seconds):
        yield


def login(browser, email, password=DEFAULT_PASSWORD, **extra):
    return post_json(browser, LOGIN_URL, {"email": email, "password": password}, **extra)


def fail_login(browser, email, times):
    return [login(browser, email, "senha-errada").status_code for _ in range(times)]


def test_after_five_failures_login_is_blocked_even_with_the_right_password(browser):
    make_user(email="ana@example.com")

    failures = fail_login(browser, "ana@example.com", FAILURE_LIMIT)
    blocked = login(browser, "ana@example.com")

    assert failures == [401] * FAILURE_LIMIT
    assert blocked.status_code == 429
    assert blocked.json() == {"detail": "Muitas tentativas. Aguarde antes de tentar novamente."}
    retry_after = int(blocked["Retry-After"])
    assert 0 < retry_after <= 15 * 60


def test_login_works_again_after_the_window_ends(browser):
    make_user(email="ana@example.com")
    fail_login(browser, "ana@example.com", FAILURE_LIMIT)
    assert login(browser, "ana@example.com").status_code == 429

    with travel(seconds=15 * 60 + 1):
        assert login(browser, "ana@example.com").status_code == 200


def test_the_wait_time_shrinks_as_the_window_runs(browser):
    make_user(email="ana@example.com")
    fail_login(browser, "ana@example.com", FAILURE_LIMIT)
    first = int(login(browser, "ana@example.com")["Retry-After"])

    with travel(seconds=60):
        later = int(login(browser, "ana@example.com")["Retry-After"])

    assert 55 <= first - later <= 61


def test_a_successful_login_resets_the_failure_counter(browser):
    make_user(email="ana@example.com")
    fail_login(browser, "ana@example.com", FAILURE_LIMIT - 1)

    assert login(browser, "ana@example.com").status_code == 200
    after_reset = fail_login(browser, "ana@example.com", FAILURE_LIMIT - 1)

    assert after_reset == [401] * (FAILURE_LIMIT - 1)
    assert login(browser, "ana@example.com").status_code == 200


def test_an_unknown_email_is_counted_and_answered_like_an_existing_account(browser):
    make_user(email="ana@example.com")

    for email in ("ana@example.com", "ninguem@example.com"):
        statuses = fail_login(browser, email, FAILURE_LIMIT)
        blocked = login(browser, email)

        assert statuses == [401] * FAILURE_LIMIT
        assert blocked.status_code == 429
        assert blocked.json() == {"detail": "Muitas tentativas. Aguarde antes de tentar novamente."}
        assert 0 < int(blocked["Retry-After"]) <= 15 * 60


def test_the_email_case_and_spaces_do_not_dodge_the_counter(browser):
    make_user(email="ana@example.com")
    variants = [
        "ana@example.com",
        "ANA@example.com",
        " Ana@Example.com ",
        "ana@EXAMPLE.com",
        "AnA@example.com",
    ]

    for email in variants:
        login(browser, email, "senha-errada")

    assert login(browser, "ana@example.com").status_code == 429


def test_the_block_is_per_account_and_client_not_global(browser):
    make_user(email="ana@example.com")
    make_user(email="bia@example.com")
    fail_login(browser, "ana@example.com", FAILURE_LIMIT)

    assert login(browser, "ana@example.com").status_code == 429
    assert login(browser, "bia@example.com").status_code == 200


@override_settings(NUM_PROXIES=1)
def test_another_client_address_is_not_blocked_by_someone_elses_failures(browser):
    make_user(email="ana@example.com")
    attacker = {"HTTP_X_FORWARDED_FOR": "203.0.113.7"}
    for _ in range(FAILURE_LIMIT):
        login(browser, "ana@example.com", "senha-errada", **attacker)

    blocked = login(browser, "ana@example.com", **attacker)
    owner = login(browser, "ana@example.com", HTTP_X_FORWARDED_FOR="198.51.100.20")

    assert blocked.status_code == 429
    assert owner.status_code == 200


def test_login_requests_are_limited_per_address_regardless_of_the_account(browser):
    users = [make_user(email=f"pessoa{n}@example.com") for n in range(22)]

    statuses = [login(browser, user.email).status_code for user in users]

    assert statuses == [200] * 20 + [429] * 2


def test_the_ip_limit_window_resets(browser):
    users = [make_user(email=f"pessoa{n}@example.com") for n in range(21)]
    statuses = [login(browser, user.email).status_code for user in users]
    assert statuses[-1] == 429

    with travel(seconds=61):
        assert login(browser, users[0].email).status_code == 200
