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
    deadline = time.monotonic() + timeout
    with connection.cursor() as cursor:
        while time.monotonic() < deadline:
            cursor.execute(
                "SELECT 1 FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )
            if cursor.fetchone():
                return
            time.sleep(0.02)
    raise AssertionError("no request ever blocked on the advising lock")


def send_while_the_advising_ends(advising, send_request):
    """Sends a request that must wait for the advising lock, then ends the advising.

    A second transaction holds the lock; once a request is blocked on it, that transaction
    ends the advising and commits, so the request runs after the end (ADR 0004). Needs a test
    marked `django_db(transaction=True)`.
    """
    holding_the_lock = threading.Event()
    failures = []

    def end_the_advising_while_holding_the_lock():
        try:
            with transaction.atomic():
                locked = Advising.objects.select_for_update().get(pk=advising.pk)
                holding_the_lock.set()
                wait_for_a_request_blocked_on_a_lock()
                close_advising(locked, "Encerrada durante a ação.")
        except Exception as error:  # noqa: BLE001 - reported to the main thread
            failures.append(error)
            holding_the_lock.set()
        finally:
            connection.close()

    ending = threading.Thread(target=end_the_advising_while_holding_the_lock)
    ending.start()
    assert holding_the_lock.wait(timeout=10)
    response = send_request()
    ending.join(timeout=10)
    assert failures == []
    return response
