"""Two runs of the reset e-mail command at the same time send each link once (RF07, ADR 0020)."""

import threading
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.db import connection

from apps.accounts.models import PasswordResetRequest
from tests.factories import make_user

pytestmark = pytest.mark.django_db(transaction=True)

COMMAND = "send_password_reset_emails"
TIMEOUT = 20


def test_a_second_run_skips_the_request_the_first_one_is_sending():
    user = make_user(email="ana@example.com")
    PasswordResetRequest.objects.create(user=user)
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
        except Exception as error:
            failures.append(error)
        finally:
            first_is_sending.set()
            connection.close()

    with patch("apps.accounts.password_reset.send_mail", side_effect=slow_first_delivery):
        runner = threading.Thread(target=first_run)
        runner.start()
        assert first_is_sending.wait(timeout=TIMEOUT)
        second_output = StringIO()
        call_command(COMMAND, stdout=second_output)
        second_is_done.set()
        runner.join(timeout=TIMEOUT)

    assert failures == []
    assert delivered == [["ana@example.com"]]
    assert "Nenhum e-mail enviado." in second_output.getvalue()
    third_output = StringIO()
    call_command(COMMAND, stdout=third_output)
    assert "Nenhum e-mail enviado." in third_output.getvalue()
    assert delivered == [["ana@example.com"]]
