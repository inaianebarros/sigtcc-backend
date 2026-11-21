"""The notice that the PDF of the report is ready, in the system and by e-mail (RN50, RF54)."""

from datetime import datetime, timedelta
from io import StringIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.core.management import call_command
from django.utils import timezone

from tests.api.helpers import bearer
from tests.api.report_builders import ended_advising
from tests.api.report_export_flow import ask_export, current_export, run_worker
from tests.factories import make_course, make_student, make_teacher

pytestmark = pytest.mark.django_db

SANTAREM = ZoneInfo("America/Santarem")
NOTIFICATIONS = "/api/notificacoes"


def notifications_of(client, user):
    return client.get(NOTIFICATIONS, headers=bearer(user)).json()["items"]


def export_ready(client, requester, advising):
    ask_export(client, requester, advising.id)
    run_worker()
    return current_export(client, requester, advising.id).json()


def send_emails(after=timedelta(minutes=1)):
    """Runs the e-mail command as the e-mail cron does, some time after the PDF got ready."""
    with patch("django.utils.timezone.now", return_value=timezone.now() + after):
        call_command("send_notification_emails", stdout=StringIO())


def day(iso_or_datetime) -> str:
    value = (
        datetime.fromisoformat(iso_or_datetime)
        if isinstance(iso_or_datetime, str)
        else iso_or_datetime
    )
    return value.astimezone(SANTAREM).strftime("%d/%m/%Y")


def test_the_requester_is_notified_when_the_pdf_is_ready(client):
    teacher = make_teacher(full_name="Paulo Professor")
    student = make_student()
    advising = ended_advising(student, teacher)

    ready = export_ready(client, student.user, advising)

    [notification] = notifications_of(client, student.user)
    assert notification["type"] == "report_export_ready"
    assert notification["message"] == (
        f"O PDF do relatório da sua orientação com Paulo Professor, encerrada em "
        f"{day(advising.ended_at)}, está pronto. Baixe-o na tela do relatório até "
        f"{day(ready['available_until'])}."
    )
    assert notification["reference"] == {"type": "advising_report", "id": advising.id}
    assert ready["notification_id"] == notification["id"]


def test_the_other_party_is_not_notified(client):
    teacher = make_teacher()
    student = make_student()
    advising = ended_advising(student, teacher)

    export_ready(client, student.user, advising)

    assert notifications_of(client, teacher.user) == []


def test_a_running_export_has_no_notification_yet(client):
    student = make_student()
    advising = ended_advising(student)

    asked = ask_export(client, student.user, advising.id).json()

    assert asked["notification_id"] is None


def test_the_e_mail_says_the_pdf_is_ready_and_links_to_the_report_screen(
    client, mailoutbox, settings
):
    settings.FRONTEND_URL = "https://sigtcc.example"
    student = make_student()
    advising = ended_advising(student, make_teacher(full_name="Paulo Professor"))
    ready = export_ready(client, student.user, advising)

    send_emails()

    [mail] = mailoutbox
    assert mail.subject == "PDF do relatório pronto"
    assert mail.to == [student.user.email]
    assert mail.attachments == []
    assert mail.body == (
        f"O PDF do relatório da sua orientação com Paulo Professor, encerrada em "
        f"{day(advising.ended_at)}, está pronto. Ele fica disponível até "
        f"{day(ready['available_until'])}, e só você pode baixá-lo.\n\n"
        "O PDF não vai anexado a este e-mail. Para baixá-lo, abra o relatório no SIGTCC (será "
        "preciso entrar com a sua conta):\n"
        f"https://sigtcc.example/aluno/historico/{advising.id}/relatorio"
    )


def test_the_teachers_e_mail_links_to_the_teachers_report_screen(client, mailoutbox, settings):
    settings.FRONTEND_URL = "https://sigtcc.example"
    teacher = make_teacher()
    advising = ended_advising(make_student(), teacher)
    export_ready(client, teacher.user, advising)

    send_emails()

    assert mailoutbox[0].body.endswith(
        f"https://sigtcc.example/professor/historico/{advising.id}/relatorio"
    )


def test_no_e_mail_when_the_person_saw_the_pdf_ready_on_the_screen(client, mailoutbox):
    student = make_student()
    advising = ended_advising(student)
    ready = export_ready(client, student.user, advising)
    client.patch(f"{NOTIFICATIONS}/{ready['notification_id']}/lida", headers=bearer(student.user))

    send_emails()
    send_emails(after=timedelta(minutes=5))

    assert mailoutbox == []


def test_the_e_mail_waits_a_few_seconds_for_the_screen_to_mark_it_read(client, mailoutbox):
    student = make_student()
    advising = ended_advising(student)
    export_ready(client, student.user, advising)

    send_emails(after=timedelta(seconds=2))
    early = len(mailoutbox)
    send_emails(after=timedelta(minutes=1))

    assert early == 0
    assert len(mailoutbox) == 1


def test_other_notifications_are_e_mailed_even_if_read(client, mailoutbox):
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student()
    advising = ended_advising(student, teacher)
    export_ready(client, student.user, advising)
    for item in notifications_of(client, student.user):
        client.patch(f"{NOTIFICATIONS}/{item['id']}/lida", headers=bearer(student.user))
    client.post(
        "/api/orientacao/solicitacoes",
        data={"teacher_id": teacher.user_id},
        content_type="application/json",
        headers=bearer(make_student(course=course).user),
    )
    for item in notifications_of(client, teacher.user):
        client.patch(f"{NOTIFICATIONS}/{item['id']}/lida", headers=bearer(teacher.user))

    send_emails()

    assert [mail.to for mail in mailoutbox] == [[teacher.user.email]]
