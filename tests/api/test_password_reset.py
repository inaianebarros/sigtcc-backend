import logging
import re
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import PasswordResetRequest, User
from tests.api.conftest import TRUSTED_ORIGIN
from tests.api.helpers import LOGIN_URL, REFRESH_COOKIE, REFRESH_URL, errors_by_field, post_json
from tests.factories import DEFAULT_PASSWORD, make_user

pytestmark = pytest.mark.django_db

FORGOT_URL = "/api/auth/forgot-password"
RESET_URL = "/api/auth/reset-password"
NEW_PASSWORD = "Nova-Senha-Segura-2026"
UID_OF_NO_ONE = "MTAwMDAw"
GENERIC_RESPONSE = {
    "detail": "Se o e-mail estiver cadastrado, enviaremos um link para redefinir a senha."
}


@contextmanager
def minutes_from_now(minutes):
    later = datetime.now() + timedelta(minutes=minutes)
    with patch.object(PasswordResetTokenGenerator, "_now", return_value=later):
        yield


COMMAND = "send_password_reset_emails"
PER_EMAIL_LIMIT = 3

# Only the per-e-mail limit is under test where this is used: the per-address one would hide it.
without_ip_limit = override_settings(
    AUTH_RATE_LIMITS={**settings.AUTH_RATE_LIMITS, "forgot_ip": (1000, 3600)}
)


def request_reset(client, email):
    return post_json(client, FORGOT_URL, {"email": email})


def send_emails():
    """The tick of the `email-cron` that e-mails the pending reset links."""
    call_command(COMMAND)


def link_parts(mailoutbox):
    body = mailoutbox[-1].body
    match = re.search(r"uid=([^&\s]+)&token=([^\s]+)", body)
    return match.group(1), match.group(2)


def reset(client, uid, token, password=NEW_PASSWORD, confirmation=None):
    return post_json(
        client,
        RESET_URL,
        {
            "uid": uid,
            "token": token,
            "password": password,
            "password_confirmation": confirmation if confirmation is not None else password,
        },
    )


def log_in(password):
    browser = Client(HTTP_ORIGIN=TRUSTED_ORIGIN)
    return post_json(browser, LOGIN_URL, {"email": "ana@example.com", "password": password})


def issued_link(client, mailoutbox, email="ana@example.com"):
    make_user(email=email)
    request_reset(client, email)
    send_emails()
    return link_parts(mailoutbox)


def test_a_registered_email_receives_a_reset_link_to_the_frontend(client, mailoutbox):
    make_user(email="ana@example.com")

    response = request_reset(client, "ana@example.com")
    assert mailoutbox == []
    send_emails()

    assert response.status_code == 200
    assert response.json() == GENERIC_RESPONSE
    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == ["ana@example.com"]
    assert "http://localhost:3000/redefinir-senha?uid=" in mailoutbox[0].body
    assert "1 hora" in mailoutbox[0].body


def test_the_email_lookup_ignores_case(client, mailoutbox):
    make_user(email="ana.souza@example.com")

    request_reset(client, "ANA.Souza@Example.com")
    send_emails()

    assert len(mailoutbox) == 1


def test_unknown_and_inactive_accounts_get_the_same_response_and_no_email(client, mailoutbox):
    inactive = make_user(email="inativo@example.com")
    inactive.is_active = False
    inactive.save()

    unknown = request_reset(client, "ninguem@example.com")
    deactivated = request_reset(client, "inativo@example.com")
    send_emails()

    assert unknown.json() == deactivated.json() == GENERIC_RESPONSE
    assert unknown.status_code == deactivated.status_code == 200
    assert mailoutbox == []


@override_settings(EMAIL_BACKEND="tests.api.failing_email_backend.FailingEmailBackend")
def test_a_failing_mail_server_does_not_reveal_which_accounts_exist(client):
    make_user(email="ana@example.com")

    existing = request_reset(client, "ana@example.com")
    unknown = request_reset(client, "ninguem@example.com")

    assert existing.status_code == unknown.status_code == 200
    assert existing.json() == unknown.json() == GENERIC_RESPONSE


def test_a_failing_mail_server_leaves_the_request_pending_and_the_next_run_sends_it(
    client, mailoutbox
):
    make_user(email="ana@example.com")
    request_reset(client, "ana@example.com")

    with override_settings(EMAIL_BACKEND="tests.api.failing_email_backend.FailingEmailBackend"):
        with pytest.raises(CommandError):
            send_emails()
    assert mailoutbox == []
    send_emails()

    assert len(mailoutbox) == 1


@pytest.mark.parametrize(("failed_runs", "e_mails_after"), [(4, 1), (5, 0)])
def test_the_command_gives_up_on_a_request_after_five_failed_attempts(
    client, mailoutbox, caplog, failed_runs, e_mails_after
):
    make_user(email="ana@example.com")
    request_reset(client, "ana@example.com")

    with override_settings(EMAIL_BACKEND="tests.api.failing_email_backend.FailingEmailBackend"):
        for _ in range(failed_runs):
            with pytest.raises(CommandError):
                send_emails()
    send_emails()

    assert len(mailoutbox) == e_mails_after
    assert ("gave up" in caplog.text) == (failed_runs == 5)


@override_settings(EMAIL_BACKEND="tests.api.failing_email_backend.FailingForOneAddressEmailBackend")
def test_a_failing_request_does_not_stop_the_others(client, mailoutbox):
    make_user(email="falha@example.com")
    make_user(email="ana@example.com")
    request_reset(client, "falha@example.com")
    request_reset(client, "ana@example.com")

    with pytest.raises(CommandError):
        send_emails()

    assert [message.to for message in mailoutbox] == [["ana@example.com"]]


@pytest.mark.parametrize(("minutes_waited", "e_mails"), [(14, 1), (16, 0)])
def test_a_request_not_sent_within_fifteen_minutes_is_discarded(
    client, mailoutbox, minutes_waited, e_mails
):
    make_user(email="ana@example.com")
    request_reset(client, "ana@example.com")

    later = timezone.now() + timedelta(minutes=minutes_waited)
    with patch("django.utils.timezone.now", return_value=later):
        send_emails()
        send_emails()

    assert len(mailoutbox) == e_mails


def test_settled_requests_are_deleted_after_a_day(client):
    make_user(email="ana@example.com")
    request_reset(client, "ana@example.com")
    request_reset(client, "ninguem@example.com")
    send_emails()
    settled_ids = list(PasswordResetRequest.objects.values_list("id", flat=True))
    request_reset(client, "ana@example.com")

    PasswordResetRequest.objects.filter(pk=settled_ids[0]).update(
        processed_at=timezone.now() - timedelta(hours=25)
    )
    PasswordResetRequest.objects.filter(pk=settled_ids[1]).update(
        processed_at=timezone.now() - timedelta(hours=23)
    )
    send_emails()

    remaining = set(PasswordResetRequest.objects.values_list("id", flat=True))
    assert settled_ids[0] not in remaining
    assert settled_ids[1] in remaining


def test_running_the_command_again_does_not_send_the_same_link_twice(client, mailoutbox):
    make_user(email="ana@example.com")
    request_reset(client, "ana@example.com")

    send_emails()
    send_emails()

    assert len(mailoutbox) == 1


def test_the_link_is_created_when_the_e_mail_is_sent_and_lasts_an_hour_from_then(
    client, mailoutbox
):
    make_user(email="ana@example.com")
    request_reset(client, "ana@example.com")
    with minutes_from_now(30):
        send_emails()
    uid, token = link_parts(mailoutbox)

    with minutes_from_now(30 + 91):
        assert reset(client, uid, token).status_code == 400
    with minutes_from_now(30 + 59):
        assert reset(client, uid, token).status_code == 200


def test_an_account_deactivated_after_the_request_gets_no_e_mail(client, mailoutbox):
    make_user(email="ana@example.com")
    request_reset(client, "ana@example.com")
    User.objects.filter(email="ana@example.com").update(is_active=False)

    send_emails()

    assert mailoutbox == []


def test_a_simulated_account_is_settled_without_an_e_mail(client, mailoutbox):
    make_user(email="ana@demo.ufopa.local")
    request_reset(client, "ana@demo.ufopa.local")

    send_emails()
    send_emails()

    assert mailoutbox == []
    request = PasswordResetRequest.objects.get()
    assert request.processed_at is not None
    assert request.attempts == 0


def test_a_similar_domain_still_receives_the_reset_link(client, mailoutbox):
    make_user(email="ana@demo.ufopa.local.example.com")
    request_reset(client, "ana@demo.ufopa.local.example.com")

    send_emails()

    assert len(mailoutbox) == 1


def test_the_request_does_the_same_work_whether_or_not_the_account_exists(client):
    make_user(email="ana@example.com")
    inactive = make_user(email="inativo@example.com")
    inactive.is_active = False
    inactive.save()

    def queries_of(email):
        with CaptureQueriesContext(connection) as captured:
            request_reset(client, email)
        return len(captured)

    existing = queries_of("ana@example.com")
    unknown = queries_of("ninguem@example.com")
    deactivated = queries_of("inativo@example.com")

    assert existing == unknown == deactivated


def test_the_queue_never_keeps_the_typed_e_mail(client):
    make_user(email="ana@example.com")

    request_reset(client, "ana@example.com")
    request_reset(client, "ninguem@example.com")

    rows = list(PasswordResetRequest.objects.values())
    assert len(rows) == 2
    assert all("ninguem@example.com" not in str(row) for row in rows)
    assert sorted(row["user_id"] is None for row in rows) == [False, True]


@without_ip_limit
def test_the_fourth_request_for_the_same_e_mail_in_an_hour_gets_the_same_answer_and_no_e_mail(
    client, mailoutbox
):
    make_user(email="ana@example.com")

    responses = [request_reset(client, "ana@example.com") for _ in range(PER_EMAIL_LIMIT + 1)]
    send_emails()

    assert [response.status_code for response in responses] == [200] * (PER_EMAIL_LIMIT + 1)
    assert all(response.json() == GENERIC_RESPONSE for response in responses)
    assert len(mailoutbox) == PER_EMAIL_LIMIT


@without_ip_limit
def test_above_the_limit_the_work_is_the_same_for_any_e_mail(client):
    make_user(email="ana@example.com")
    inactive = make_user(email="inativo@example.com")
    inactive.is_active = False
    inactive.save()

    def queries_of(email):
        with CaptureQueriesContext(connection) as captured:
            request_reset(client, email)
        return len(captured)

    emails = ["ana@example.com", "ninguem@example.com", "inativo@example.com"]
    within_limit = queries_of("novo@example.com")
    for email in emails:
        for _ in range(PER_EMAIL_LIMIT - 1):
            request_reset(client, email)
        assert queries_of(email) == within_limit

    above_limit = {email: queries_of(email) for email in emails}

    assert len(set(above_limit.values())) == 1
    assert above_limit["ninguem@example.com"] < within_limit


@without_ip_limit
def test_the_limit_counts_one_e_mail_across_addresses_and_spellings(client, mailoutbox):
    make_user(email="ana.souza@example.com")
    spellings = ["ana.souza@example.com", "ANA.Souza@Example.com", "ana.souza@example.com"]

    for number, email in enumerate(spellings):
        post_json(client, FORGOT_URL, {"email": email}, REMOTE_ADDR=f"10.0.0.{number + 1}")
    send_emails()
    assert len(mailoutbox) == PER_EMAIL_LIMIT

    for number, email in enumerate(["ana.souza@example.com", "  ANA.Souza@example.com  "]):
        with CaptureQueriesContext(connection) as captured:
            post_json(client, FORGOT_URL, {"email": email}, REMOTE_ADDR=f"10.0.1.{number + 1}")
        assert len(captured) == 0


@without_ip_limit
def test_the_limit_of_one_e_mail_does_not_affect_another(client, mailoutbox):
    make_user(email="ana@example.com")
    make_user(email="bia@example.com")
    for _ in range(PER_EMAIL_LIMIT + 1):
        request_reset(client, "ana@example.com")

    request_reset(client, "bia@example.com")
    send_emails()

    assert [message.to for message in mailoutbox].count(["bia@example.com"]) == 1


def test_a_broken_cache_does_not_stop_the_request_and_the_failure_is_logged(
    client, mailoutbox, caplog
):
    make_user(email="ana@example.com")

    with patch("django.core.cache.cache.add", side_effect=ConnectionError("cache fora do ar")):
        response = request_reset(client, "ana@example.com")
    send_emails()

    assert response.status_code == 200
    assert response.json() == GENERIC_RESPONSE
    assert len(mailoutbox) == 1
    assert any(
        record.levelno >= logging.ERROR and getattr(record, "scope", "") == "forgot_account"
        for record in caplog.records
    )


@without_ip_limit
def test_the_limit_is_logged_without_the_e_mail_the_token_or_a_password(client, caplog):
    make_user(email="ana@example.com")

    with caplog.at_level(logging.WARNING, logger="sigtcc.auth"):
        for _ in range(PER_EMAIL_LIMIT + 1):
            request_reset(client, "ana@example.com")

    limited = [
        record for record in caplog.records if getattr(record, "scope", "") == "forgot_account"
    ]
    assert len(limited) == 1
    logged = " ".join(f"{record.getMessage()} {vars(record)}" for record in limited)
    assert "ana@example.com" not in logged
    assert "senha" not in logged.lower()
    assert "token" not in logged.lower()


@without_ip_limit
def test_the_limit_window_is_configurable(client, mailoutbox):
    make_user(email="ana@example.com")
    custom = {**settings.AUTH_RATE_LIMITS, "forgot_account": (1, 3600)}

    with override_settings(AUTH_RATE_LIMITS=custom):
        request_reset(client, "ana@example.com")
        request_reset(client, "ana@example.com")
    send_emails()

    assert len(mailoutbox) == 1


def test_reset_requests_are_rate_limited_per_client(client, mailoutbox):
    statuses = [request_reset(client, f"pessoa{n}@example.com").status_code for n in range(6)]

    assert statuses == [200] * 5 + [429]


def test_the_request_requires_an_email_field(client):
    assert post_json(client, FORGOT_URL, {}).status_code == 422


def test_the_link_sets_a_new_password_and_the_old_one_stops_working(client, mailoutbox):
    uid, token = issued_link(client, mailoutbox)

    response = reset(client, uid, token)

    assert response.status_code == 200
    assert log_in(NEW_PASSWORD).status_code == 200
    assert log_in(DEFAULT_PASSWORD).status_code == 401


def test_a_link_can_be_used_only_once(client, mailoutbox):
    uid, token = issued_link(client, mailoutbox)
    assert reset(client, uid, token).status_code == 200

    assert reset(client, uid, token, password="Outra-Senha-Forte-2026").status_code == 400


def test_the_link_is_valid_for_just_under_an_hour(client, mailoutbox):
    uid, token = issued_link(client, mailoutbox)

    with minutes_from_now(59):
        assert reset(client, uid, token).status_code == 200


def test_the_link_expires_after_one_hour(client, mailoutbox):
    uid, token = issued_link(client, mailoutbox)

    with minutes_from_now(61):
        assert reset(client, uid, token).status_code == 400


@pytest.mark.parametrize("tamper", ["token", "uid"])
def test_a_tampered_link_is_rejected(client, mailoutbox, tamper):
    uid, token = issued_link(client, mailoutbox)
    if tamper == "token":
        token = token[:-1] + ("a" if token[-1] != "a" else "b")
    else:
        uid = UID_OF_NO_ONE

    assert reset(client, uid, token).status_code == 400


def test_a_deactivated_account_cannot_reset_its_password(client, mailoutbox):
    uid, token = issued_link(client, mailoutbox)
    User.objects.filter(email="ana@example.com").update(is_active=False)

    assert reset(client, uid, token).status_code == 400


def test_the_new_password_must_match_its_confirmation(client, mailoutbox):
    uid, token = issued_link(client, mailoutbox)

    response = reset(client, uid, token, confirmation="Diferente-Senha-2026")

    assert response.status_code == 422
    assert "password_confirmation" in errors_by_field(response)


def test_a_weak_password_is_rejected_and_the_link_stays_usable(client, mailoutbox):
    uid, token = issued_link(client, mailoutbox)

    weak = reset(client, uid, token, password="password")

    assert weak.status_code == 422
    assert "password" in errors_by_field(weak)
    assert reset(client, uid, token).status_code == 200


def test_resetting_the_password_ends_every_active_session(client, mailoutbox):
    uid, token = issued_link(client, mailoutbox)
    browser = Client(HTTP_ORIGIN=TRUSTED_ORIGIN)
    post_json(browser, LOGIN_URL, {"email": "ana@example.com", "password": DEFAULT_PASSWORD})
    assert browser.cookies[REFRESH_COOKIE].value

    assert reset(client, uid, token).status_code == 200

    assert browser.post(REFRESH_URL).status_code == 401
