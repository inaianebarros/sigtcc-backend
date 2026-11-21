"""The failure of the export of the report and the request of a deactivated account (RN50, RN19)."""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.core.files.storage import storages
from django.core.management import CommandError, call_command
from django.utils import timezone

from apps.accounts.deactivation import deactivate_user
from apps.notifications.models import Notification
from tests.api.helpers import bearer
from tests.api.report_builders import ended_advising
from tests.api.report_export_flow import ask_export, current_export, download, run_worker
from tests.factories import make_student, make_teacher

pytestmark = pytest.mark.django_db

SANTAREM = ZoneInfo("America/Santarem")
NOTIFICATIONS = "/api/notificacoes"
FAILING = {"BACKEND": "tests.api.failing_storage.FailingStorage"}


@pytest.fixture
def bucket(settings):
    """Switches the bucket off and on, as an outage that ends."""
    working = settings.STORAGES

    class Bucket:
        def down(self):
            settings.STORAGES = {**working, "submissions": FAILING}

        def up(self):
            settings.STORAGES = working

    return Bucket()


def stored_pdfs(advising_id):
    try:
        return storages["submissions"].listdir(f"exportacoes/{advising_id}")[1]
    except FileNotFoundError:
        return []


@pytest.fixture(autouse=True)
def clock():
    """A clock the test moves: the worker waits between attempts (RN50)."""
    start = timezone.now()

    class Clock:
        offset = timedelta()

        def now(self):
            return start + self.offset

        def advance(self, by):
            self.offset += by

    moving = Clock()
    with patch("django.utils.timezone.now", side_effect=moving.now):
        yield moving


def run_failing(times, clock):
    for _ in range(times):
        with pytest.raises(CommandError):
            run_worker()
        clock.advance(timedelta(minutes=10))


def notifications_of(client, user):
    return client.get(NOTIFICATIONS, headers=bearer(user)).json()["items"]


def send_emails(clock):
    clock.advance(timedelta(minutes=1))
    call_command("send_notification_emails", stdout=StringIO())


def test_a_failure_puts_the_export_back_in_the_queue_until_the_fifth_attempt(client, bucket, clock):
    student = make_student()
    advising = ended_advising(student)
    ask_export(client, student.user, advising.id)
    bucket.down()

    run_failing(4, clock)
    retrying = current_export(client, student.user, advising.id).json()
    run_failing(1, clock)
    failed = current_export(client, student.user, advising.id).json()

    assert retrying["status"] == "queued" and retrying["stage"] == "queued"
    assert failed["status"] == "failed"
    assert download(client, student.user, advising.id).status_code == 409


def test_a_bucket_that_comes_back_before_the_fifth_attempt_gets_the_pdf_built(
    client, bucket, clock
):
    student = make_student()
    advising = ended_advising(student)
    ask_export(client, student.user, advising.id)
    bucket.down()
    run_failing(2, clock)
    bucket.up()

    run_worker()

    assert download(client, student.user, advising.id).status_code == 200
    assert [item["type"] for item in notifications_of(client, student.user)] == [
        "report_export_ready"
    ]


def test_after_the_fifth_failure_the_requester_is_notified(client, bucket, clock):
    teacher = make_teacher(full_name="Paulo Professor")
    student = make_student()
    advising = ended_advising(student, teacher)
    ask_export(client, student.user, advising.id)
    bucket.down()

    run_failing(5, clock)

    [notification] = notifications_of(client, student.user)
    ended = advising.ended_at.astimezone(SANTAREM).strftime("%d/%m/%Y")
    assert notification["type"] == "report_export_failed"
    assert notification["message"] == (
        f"Não foi possível gerar o PDF do relatório da sua orientação com Paulo Professor, "
        f"encerrada em {ended}. Abra o relatório e use o botão Tentar novamente."
    )
    assert notification["reference"] == {"type": "advising_report", "id": advising.id}
    assert (
        current_export(client, student.user, advising.id).json()["notification_id"]
        == notification["id"]
    )
    assert notifications_of(client, teacher.user) == []


def test_after_a_failure_asking_again_builds_a_new_pdf(client, bucket, clock):
    student = make_student()
    advising = ended_advising(student)
    failed = ask_export(client, student.user, advising.id).json()
    bucket.down()
    run_failing(5, clock)
    bucket.up()

    again = ask_export(client, student.user, advising.id).json()
    run_worker()

    assert again["id"] != failed["id"]
    assert download(client, student.user, advising.id).status_code == 200


def test_the_failure_e_mail_links_to_the_report_screen(client, bucket, clock, mailoutbox, settings):
    settings.FRONTEND_URL = "https://sigtcc.example"
    teacher = make_teacher()
    advising = ended_advising(make_student(full_name="Ana Aluna"), teacher)
    ask_export(client, teacher.user, advising.id)
    bucket.down()
    run_failing(5, clock)

    send_emails(clock)

    [mail] = mailoutbox
    ended = advising.ended_at.astimezone(SANTAREM).strftime("%d/%m/%Y")
    assert mail.subject == "PDF do relatório não gerado"
    assert mail.body == (
        f"Não foi possível gerar o PDF do relatório da sua orientação com Ana Aluna, "
        f"encerrada em {ended}.\n\n"
        "Para pedir de novo, abra o relatório no SIGTCC (será preciso entrar com a sua conta) "
        "e use o botão Tentar novamente:\n"
        f"https://sigtcc.example/professor/historico/{advising.id}/relatorio"
    )


def test_no_failure_e_mail_when_the_person_saw_it_on_the_screen(client, bucket, clock, mailoutbox):
    student = make_student()
    advising = ended_advising(student)
    ask_export(client, student.user, advising.id)
    bucket.down()
    run_failing(5, clock)
    failed = current_export(client, student.user, advising.id).json()
    client.patch(f"{NOTIFICATIONS}/{failed['notification_id']}/lida", headers=bearer(student.user))

    send_emails(clock)

    assert mailoutbox == []


def test_the_request_of_an_account_deactivated_while_queued_is_dropped_silently(client):
    student = make_student()
    advising = ended_advising(student)
    ask_export(client, student.user, advising.id)
    deactivate_user(student.user)

    run_worker()

    assert stored_pdfs(advising.id) == []
    # The account can no longer sign in to read its notifications: none was created for it.
    assert not Notification.objects.filter(recipient=student.user).exists()


def test_the_worker_waits_longer_after_each_failure_before_trying_again(client, bucket, clock):
    student = make_student()
    advising = ended_advising(student)
    ask_export(client, student.user, advising.id)
    bucket.down()
    with pytest.raises(CommandError):
        run_worker()
    bucket.up()

    run_worker()
    too_soon = current_export(client, student.user, advising.id).json()
    clock.advance(timedelta(seconds=31))
    run_worker()

    assert too_soon["status"] == "queued"
    assert download(client, student.user, advising.id).status_code == 200
