import threading
import time
from contextlib import contextmanager
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from ninja_jwt.tokens import RefreshToken

from apps.advising.models import Advising
from apps.advising.services import close_advising

PASSWORD = "Senha-Segura-2026"

LOGIN_URL = "/api/auth/login"
REFRESH_URL = "/api/auth/refresh"
LOGOUT_URL = "/api/auth/logout"
CHANGE_PASSWORD_URL = "/api/auth/change-password"
NEW_PASSWORD = "Nova-Senha-Segura-2026"
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


CREATE_NOTIFICATION = "apps.notifications.services.Notification.objects.create"


def inbox(client, user):
    """The notifications of a user, as the screen lists them (newest first)."""
    return client.get("/api/notificacoes", headers=bearer(user)).json()["items"]


TOO_MANY_REQUESTS = {"detail": "Muitas tentativas. Aguarde antes de tentar novamente."}


@contextmanager
def travel(seconds):
    """Move the clock seen by the cache and the rate-limit counters forward."""
    real_time = time.time
    with patch("time.time", side_effect=lambda: real_time() + seconds):
        yield


def wait_for_a_request_blocked_on_a_lock(timeout=10):
    """Waits until another connection is blocked on a lock.

    Inside an open transaction PostgreSQL keeps one snapshot of `pg_stat_activity` until the
    transaction ends, so the snapshot is cleared before each look.
    """
    deadline = time.monotonic() + timeout
    with connection.cursor() as cursor:
        while time.monotonic() < deadline:
            cursor.execute("SELECT pg_stat_clear_snapshot()")
            cursor.execute(
                "SELECT 1 FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )
            if cursor.fetchone():
                return
            time.sleep(0.02)
    raise AssertionError("no request ever blocked on the advising lock")


def send_while_a_lock_is_held(lock_and_act, send_request):
    """Sends a request that must wait for a row lock, then lets the lock holder act and commit.

    `lock_and_act(wait_for_the_request)` runs in a second transaction: it takes the lock, calls
    `wait_for_the_request()` once, and does its work, which commits after that. The request is
    sent only once the lock is held, so it runs after the work (ADR 0004). Needs a test marked
    `django_db(transaction=True)`.
    """
    holding_the_lock = threading.Event()
    failures = []

    def hold_the_lock():
        try:
            with transaction.atomic():
                lock_and_act(lambda: _announce_and_wait(holding_the_lock))
        except Exception as error:  # noqa: BLE001 - reported to the main thread
            failures.append(error)
            holding_the_lock.set()
        finally:
            connection.close()

    holder = threading.Thread(target=hold_the_lock)
    holder.start()
    assert holding_the_lock.wait(timeout=10)
    response = send_request()
    holder.join(timeout=20)
    assert failures == []
    return response


def _announce_and_wait(holding_the_lock):
    holding_the_lock.set()
    wait_for_a_request_blocked_on_a_lock(timeout=20)


def send_while_the_advising_ends(advising, send_request):
    """Sends a request that must wait for the advising lock, then ends the advising."""

    def end_the_advising(wait_for_the_request):
        locked = Advising.objects.select_for_update().get(pk=advising.pk)
        wait_for_the_request()
        close_advising(locked, "Encerrada durante a ação.")

    return send_while_a_lock_is_held(end_the_advising, send_request)
