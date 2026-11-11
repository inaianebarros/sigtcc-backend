"""Two runs of the e-mail command at the same time send each notification once (RF54, #155)."""

import threading
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.db import connection

from apps.notifications.models import Notification, NotificationType
from tests.factories import make_notification, make_teacher

pytestmark = pytest.mark.django_db(transaction=True)

COMMAND = "send_notification_emails"
TIMEOUT = 20


def test_a_second_run_skips_the_notification_the_first_one_is_sending():
    notification = make_notification(
        make_teacher().user,
        NotificationType.ADVISING_REQUEST_RECEIVED,
        payload={"student_name": "Ana Souza"},
    )
    delivered = []
    first_is_sending = threading.Event()
    second_is_done = threading.Event()
    first_call = threading.Lock()
    failures = []

    def slow_first_delivery(subject, body, from_email, recipients):
        if first_call.acquire(blocking=False):
            first_is_sending.set()
            assert second_is_done.wait(timeout=TIMEOUT)
        delivered.append(recipients)

    def first_run():
        try:
            call_command(COMMAND, stdout=StringIO())
        except Exception as error:  # noqa: BLE001 - reported to the main thread
            failures.append(error)
        finally:
            first_is_sending.set()
            connection.close()

    with patch("apps.notifications.emails.send_mail", side_effect=slow_first_delivery):
        runner = threading.Thread(target=first_run)
        runner.start()
        assert first_is_sending.wait(timeout=TIMEOUT)
        second_output = StringIO()
        call_command(COMMAND, stdout=second_output)
        second_is_done.set()
        runner.join(timeout=TIMEOUT)

    assert failures == []
    assert delivered == [[notification.recipient.email]]
    assert "Nenhum e-mail enviado." in second_output.getvalue()
    notification.refresh_from_db()
    assert notification.email_sent_at is not None
    assert notification.email_attempts == 0
    assert Notification.objects.count() == 1
