import json
import logging

import pytest

from config.logging import JsonFormatter
from tests.api.helpers import LOGIN_URL, post_json
from tests.factories import make_user

pytestmark = pytest.mark.django_db

EMAIL = "ana.souza@example.com"
WRONG_PASSWORD = "senha-errada-123"


@pytest.fixture
def auth_log(caplog):
    logger = logging.getLogger("sigtcc.auth")
    logger.addHandler(caplog.handler)
    caplog.set_level(logging.INFO, logger="sigtcc.auth")
    yield caplog
    logger.removeHandler(caplog.handler)


def events(caplog, name):
    return [record for record in caplog.records if getattr(record, "event", None) == name]


def attempt(browser, email=EMAIL, password=WRONG_PASSWORD):
    return post_json(browser, LOGIN_URL, {"email": email, "password": password})


def test_each_failure_is_logged_with_the_user_id_and_the_running_count(browser, auth_log):
    user = make_user(email=EMAIL)

    for _ in range(3):
        attempt(browser)

    failed = events(auth_log, "login_failed")
    assert [(record.user_id, record.failures) for record in failed] == [
        (user.id, 1),
        (user.id, 2),
        (user.id, 3),
    ]


def test_a_failure_for_an_unknown_email_is_logged_without_a_user_id(browser, auth_log):
    attempt(browser, email="ninguem@example.com")

    (record,) = events(auth_log, "login_failed")
    assert record.user_id is None
    assert record.failures == 1


def test_the_block_is_logged_with_the_failure_count_and_the_wait_time(browser, auth_log):
    make_user(email=EMAIL)
    for _ in range(5):
        attempt(browser)

    attempt(browser)

    (record,) = events(auth_log, "login_blocked")
    assert record.failures == 5
    assert 0 < record.retry_after <= 15 * 60
    assert record.levelno == logging.WARNING


def test_the_ip_limit_is_logged_with_its_scope(browser, auth_log):
    for _ in range(21):
        attempt(browser, email="ninguem@example.com")

    assert [record.scope for record in events(auth_log, "rate_limited")] == ["login_ip"]


def test_the_logs_never_carry_the_password_the_token_or_the_email_in_clear(browser, auth_log):
    make_user(email=EMAIL)
    for _ in range(6):
        attempt(browser)

    formatter = JsonFormatter()
    lines = [
        formatter.format(record) for record in auth_log.records if record.name == "sigtcc.auth"
    ]
    assert lines
    for line in lines:
        assert EMAIL not in line
        assert WRONG_PASSWORD not in line
        assert "token" not in json.loads(line)
