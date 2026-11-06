import pytest
from django.test import RequestFactory

from apps.accounts.failure_limit import clear_failures, ensure_not_blocked, record_failure
from config.errors import TooManyRequestsError

pytestmark = pytest.mark.django_db

SCOPE = "change_password_failures"


def request_from(address="203.0.113.7"):
    return RequestFactory().post("/", REMOTE_ADDR=address)


def test_the_change_password_scope_blocks_after_five_failures_of_the_same_account_and_address():
    request = request_from()
    for _ in range(5):
        ensure_not_blocked(SCOPE, "42", request)
        record_failure(SCOPE, "42", request, user_id=42)

    with pytest.raises(TooManyRequestsError) as blocked:
        ensure_not_blocked(SCOPE, "42", request)

    assert 0 < blocked.value.retry_after <= 15 * 60


def test_the_scope_counts_per_account_and_per_address():
    request = request_from()
    for _ in range(5):
        record_failure(SCOPE, "42", request, user_id=42)

    ensure_not_blocked(SCOPE, "43", request)
    ensure_not_blocked(SCOPE, "42", request_from("198.51.100.9"))


def test_a_success_resets_the_counter_of_the_account():
    request = request_from()
    for _ in range(4):
        record_failure(SCOPE, "42", request, user_id=42)

    clear_failures(SCOPE, "42", request)
    for _ in range(4):
        record_failure(SCOPE, "42", request, user_id=42)

    ensure_not_blocked(SCOPE, "42", request)


def test_the_scopes_do_not_share_counters():
    request = request_from()
    for _ in range(5):
        record_failure(SCOPE, "42", request, user_id=42)

    ensure_not_blocked("login_failures", "42", request)
