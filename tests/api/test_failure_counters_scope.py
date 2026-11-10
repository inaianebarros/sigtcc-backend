"""A success clears only the counter of the account and address in use (#88, ADR 0007).

Clearing the counters of the account at every address would let an attacker reset their own limit
whenever the real owner signs in.
"""

import pytest
from django.test import override_settings

from tests.api.helpers import (
    CHANGE_PASSWORD_URL,
    LOGIN_URL,
    NEW_PASSWORD,
    TOO_MANY_REQUESTS,
    bearer,
    post_json,
)
from tests.factories import DEFAULT_PASSWORD, make_user

pytestmark = pytest.mark.django_db

FAILURE_LIMIT = 5
OWNER = {"HTTP_X_FORWARDED_FOR": "198.51.100.20"}
ATTACKER = {"HTTP_X_FORWARDED_FOR": "203.0.113.7"}


def login(browser, email, password, **extra):
    return post_json(browser, LOGIN_URL, {"email": email, "password": password}, **extra)


@override_settings(NUM_PROXIES=1)
def test_a_login_success_clears_the_counter_of_its_address_and_not_the_other_one(browser):
    make_user(email="ana@example.com")
    for _ in range(FAILURE_LIMIT - 1):
        login(browser, "ana@example.com", "senha-errada", **ATTACKER)
        login(browser, "ana@example.com", "senha-errada", **OWNER)

    assert login(browser, "ana@example.com", DEFAULT_PASSWORD, **OWNER).status_code == 200

    # The owner's counter is back to zero: four more failures do not block it.
    owner_failures = [
        login(browser, "ana@example.com", "senha-errada", **OWNER).status_code
        for _ in range(FAILURE_LIMIT - 1)
    ]
    assert owner_failures == [401] * (FAILURE_LIMIT - 1)
    # The attacker's counter was not touched: one more failure reaches the limit, then it blocks.
    assert login(browser, "ana@example.com", "senha-errada", **ATTACKER).status_code == 401
    blocked = login(browser, "ana@example.com", "senha-errada", **ATTACKER)
    assert blocked.status_code == 429
    assert blocked.json() == TOO_MANY_REQUESTS


@override_settings(NUM_PROXIES=1)
def test_a_change_of_password_success_clears_only_its_own_address(browser):
    user = make_user()
    headers = bearer(user)

    def change(current_password, **extra):
        body = {
            "current_password": current_password,
            "password": NEW_PASSWORD,
            "password_confirmation": NEW_PASSWORD,
        }
        return post_json(browser, CHANGE_PASSWORD_URL, body, headers=headers, **extra)

    for _ in range(FAILURE_LIMIT - 1):
        change("senha-errada-123", **ATTACKER)
        change("senha-errada-123", **OWNER)

    assert change(DEFAULT_PASSWORD, **OWNER).status_code == 200

    # The password is now NEW_PASSWORD: the old one is wrong for both addresses from here on.
    owner_failures = [
        change(DEFAULT_PASSWORD, **OWNER).status_code for _ in range(FAILURE_LIMIT - 1)
    ]
    assert owner_failures == [422] * (FAILURE_LIMIT - 1)
    assert change(DEFAULT_PASSWORD, **ATTACKER).status_code == 422
    assert change(DEFAULT_PASSWORD, **ATTACKER).status_code == 429
