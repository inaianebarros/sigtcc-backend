"""E-mail through the Brevo HTTPS API instead of SMTP (issue #300, ADRs 0011 and 0021)."""

import io
import json
import logging
import os
import subprocess
import sys
from email.message import Message
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest
from django.core import mail
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMessage, get_connection, send_mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.accounts.models import PasswordResetRequest
from apps.notifications.models import NotificationType
from config import brevo_email
from config.brevo_email import BrevoDeliveryError
from tests.api.helpers import post_json
from tests.factories import make_notification, make_teacher, make_user

pytestmark = pytest.mark.django_db

API_KEY = "xkeysib-super-secret-key-1234567890"
BREVO_SETTINGS = {
    "EMAIL_BACKEND": "config.brevo_email.BrevoEmailBackend",
    "BREVO_API_KEY": API_KEY,
    "BREVO_API_URL": "https://api.brevo.test/v3/smtp/email",
    "BREVO_API_TIMEOUT_SECONDS": 7,
    "DEFAULT_FROM_EMAIL": "verificado@example.com",
    "EMAIL_FROM_NAME": "SIGTCC",
}
RECEIVED = {"student_name": "Ana Souza", "request_id": 1}

brevo = override_settings(**BREVO_SETTINGS)


def api_response(status=201, body=b'{"messageId": "<abc>"}'):
    response = MagicMock()
    response.status = status
    response.__enter__.return_value = response
    response.read.return_value = body
    return response


def http_error(status, body=b'{"code": "invalid_parameter", "message": "Sender is invalid"}'):
    return HTTPError(BREVO_SETTINGS["BREVO_API_URL"], status, "error", Message(), io.BytesIO(body))


@pytest.fixture
def api():
    """The HTTPS transport of the backend; no test reaches the network."""
    with patch.object(brevo_email._OPENER, "open", return_value=api_response()) as transport:
        yield transport


@pytest.fixture
def no_smtp():
    with patch("smtplib.SMTP", side_effect=AssertionError("SMTP must not be used")) as smtp:
        yield smtp


def pending_notification():
    return make_notification(
        make_teacher().user, NotificationType.ADVISING_REQUEST_RECEIVED, payload=RECEIVED
    )


@brevo
def test_a_message_goes_to_the_brevo_api_as_plain_text_without_opening_smtp(api, no_smtp):
    sent = send_mail("Assunto", "Corpo em texto", None, ["ana@example.com"])

    assert sent == 1
    no_smtp.assert_not_called()
    (request,), options = api.call_args
    assert request.full_url == BREVO_SETTINGS["BREVO_API_URL"]
    assert request.get_method() == "POST"
    assert request.get_header("Api-key") == API_KEY
    assert options["timeout"] == 7
    assert json.loads(request.data) == {
        "sender": {"email": "verificado@example.com", "name": "SIGTCC"},
        "to": [{"email": "ana@example.com"}],
        "subject": "Assunto",
        "textContent": "Corpo em texto",
    }


@brevo
def test_the_notification_command_sends_through_the_api_and_settles_the_notification(api, no_smtp):
    notification = pending_notification()

    call_command("send_notification_emails")

    notification.refresh_from_db()
    assert notification.email_sent_at is not None
    assert api.call_count == 1
    no_smtp.assert_not_called()


@brevo
def test_the_password_reset_command_sends_through_the_api(client, api, no_smtp):
    make_user(email="ana@example.com")
    post_json(client, "/api/auth/forgot-password", {"email": "ana@example.com"})

    call_command("send_password_reset_emails")

    (request,), _ = api.call_args
    assert json.loads(request.data)["to"] == [{"email": "ana@example.com"}]
    assert "redefinir-senha?uid=" in json.loads(request.data)["textContent"]
    assert PasswordResetRequest.objects.get().processed_at is not None


@brevo
def test_a_simulated_account_is_settled_without_calling_the_api(api):
    notification = make_notification(
        make_user(email="aluno.simulado@demo.ufopa.local"),
        NotificationType.ADVISING_REQUEST_RECEIVED,
        payload=RECEIVED,
    )

    call_command("send_notification_emails")

    api.assert_not_called()
    notification.refresh_from_db()
    assert notification.email_sent_at is not None
    assert notification.email_attempts == 0


@brevo
@pytest.mark.parametrize(
    "failure",
    [
        http_error(503, b"<html>unavailable</html>"),
        http_error(429, b'{"code": "out_of_calls", "message": "daily quota"}'),
        http_error(400),
        http_error(401, b'{"code": "unauthorized"}'),
        TimeoutError("timed out"),
        URLError("connection refused"),
        ConnectionResetError("reset"),
    ],
    ids=["503", "429-quota", "400-sender", "401-key", "timeout", "unreachable", "reset"],
)
def test_an_api_failure_counts_the_attempt_and_the_queue_goes_on(api, failure):
    failing = pending_notification()
    healthy = pending_notification()
    api.side_effect = [failure, api_response()]

    with pytest.raises(CommandError, match=str(failing.pk)):
        call_command("send_notification_emails")

    failing.refresh_from_db()
    healthy.refresh_from_db()
    assert (failing.email_sent_at, failing.email_attempts) == (None, 1)
    assert healthy.email_sent_at is not None


@brevo
def test_the_notification_is_given_up_after_five_failed_attempts_and_logged(api, caplog):
    notification = pending_notification()
    api.side_effect = http_error(503)

    for _ in range(5):
        with pytest.raises(CommandError):
            call_command("send_notification_emails")
    call_command("send_notification_emails")

    notification.refresh_from_db()
    assert notification.email_attempts == 5
    assert notification.email_sent_at is None
    assert api.call_count == 5
    assert "gave up" in caplog.text


@brevo
def test_a_failed_password_reset_stays_pending_and_the_next_run_sends_it(client, api):
    make_user(email="ana@example.com")
    post_json(client, "/api/auth/forgot-password", {"email": "ana@example.com"})
    api.side_effect = [http_error(503), api_response()]

    with pytest.raises(CommandError):
        call_command("send_password_reset_emails")
    assert PasswordResetRequest.objects.get().processed_at is None
    call_command("send_password_reset_emails")

    assert PasswordResetRequest.objects.get().processed_at is not None


@brevo
@pytest.mark.parametrize(
    "failure",
    [
        http_error(401),
        http_error(400),
        http_error(503, b"not json"),
        TimeoutError(f"timed out talking to {API_KEY}"),
        URLError(f"failed with {API_KEY}"),
    ],
)
def test_the_api_key_is_neither_in_the_exception_nor_in_the_logs(api, caplog, failure):
    pending_notification()
    api.side_effect = failure

    with caplog.at_level(logging.DEBUG), pytest.raises(CommandError) as raised:
        call_command("send_notification_emails")

    assert API_KEY not in caplog.text
    assert API_KEY not in str(raised.value)
    assert API_KEY not in "".join(record.getMessage() for record in caplog.records)
    assert "Brevo API" in caplog.text


@brevo
def test_the_delivery_error_describes_the_failure_without_echoing_the_body(api):
    api.side_effect = http_error(400)

    with pytest.raises(
        BrevoDeliveryError, match=r"HTTP 400 \(invalid_parameter\).*unverified sender"
    ):
        send_mail("a", "b", None, ["ana@example.com"])


@brevo
def test_fail_silently_swallows_the_delivery_error(api):
    api.side_effect = http_error(503)

    assert send_mail("a", "b", None, ["ana@example.com"], fail_silently=True) == 0


@brevo
def test_a_redirect_is_not_followed_so_the_key_is_never_resent():
    handler = brevo_email._NoRedirects()

    assert (
        handler.redirect_request(MagicMock(), MagicMock(), 302, "Found", {}, "http://evil") is None
    )


@brevo
def test_a_message_with_attachments_is_refused_instead_of_losing_them(api):
    message = EmailMessage("a", "b", None, ["ana@example.com"])
    message.attach("a.txt", "x", "text/plain")

    with pytest.raises(BrevoDeliveryError):
        get_connection().send_messages([message])
    api.assert_not_called()


@override_settings(**{**BREVO_SETTINGS, "BREVO_API_KEY": ""})
def test_the_backend_without_a_key_is_a_configuration_error():
    with pytest.raises(ImproperlyConfigured, match="BREVO_API_KEY"):
        get_connection()


@override_settings(**{**BREVO_SETTINGS, "BREVO_API_URL": "http://api.brevo.test"})
def test_the_backend_refuses_a_plain_http_api_url():
    with pytest.raises(ImproperlyConfigured, match="https"):
        get_connection()


def test_the_default_configuration_keeps_smtp_and_never_calls_the_api(api):
    send_mail("a", "b", None, ["ana@example.com"])

    api.assert_not_called()
    assert len(mail.outbox) == 1


def load_settings(**environment):
    """Imports the settings module in a clean process with the given e-mail variables."""
    base = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("BREVO_", "EMAIL_")) and key != "DEFAULT_FROM_EMAIL"
    }
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import config.settings as s; print(getattr(s, 'EMAIL_BACKEND', 'django-default'))",
        ],
        env={**base, **environment},
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_settings_pick_the_brevo_backend_when_configured():
    result = load_settings(
        EMAIL_PROVIDER="brevo", BREVO_API_KEY=API_KEY, DEFAULT_FROM_EMAIL="v@example.com"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "config.brevo_email.BrevoEmailBackend"


def test_the_settings_keep_smtp_by_default():
    result = load_settings()

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "django-default"


@pytest.mark.parametrize(
    "environment",
    [
        {"EMAIL_PROVIDER": "brevo", "DEFAULT_FROM_EMAIL": "v@example.com"},
        {"EMAIL_PROVIDER": "brevo", "BREVO_API_KEY": API_KEY},
        {
            "EMAIL_PROVIDER": "brevo",
            "BREVO_API_KEY": API_KEY,
            "DEFAULT_FROM_EMAIL": "v@example.com",
            "BREVO_API_URL": "http://insecure.example.com",
        },
        {"EMAIL_PROVIDER": "sendgrid"},
    ],
    ids=["no-key", "no-sender", "http-url", "unknown-provider"],
)
def test_a_bad_e_mail_configuration_fails_at_startup_without_printing_the_key(environment):
    result = load_settings(**environment)

    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert API_KEY not in result.stderr + result.stdout


@brevo
def test_the_quota_exceeded_is_logged_as_a_warning_with_a_stable_event_name(api, caplog):
    pending_notification()
    api.side_effect = http_error(429, b'{"code": "out_of_calls"}')

    with pytest.raises(CommandError):
        call_command("send_notification_emails")

    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert any("email_daily_quota_exceeded" in record.getMessage() for record in warnings)
    assert API_KEY not in caplog.text


@brevo
@pytest.mark.parametrize("status", [429, 503, 401])
def test_a_mail_failure_never_reaches_the_user_and_the_forgot_response_is_the_same(
    client, api, status
):
    make_user(email="ana@example.com")
    api.side_effect = http_error(status)

    existing = post_json(client, "/api/auth/forgot-password", {"email": "ana@example.com"})
    unknown = post_json(client, "/api/auth/forgot-password", {"email": "ninguem@example.com"})

    assert existing.status_code == unknown.status_code == 200
    assert existing.json() == unknown.json()
    api.assert_not_called()


@brevo
def test_creating_a_notification_does_not_touch_the_mail_api(api):
    pending_notification()

    api.assert_not_called()
