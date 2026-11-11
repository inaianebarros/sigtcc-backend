"""The command that e-mails each pending notification to its recipient (RF54, RNF04, #153)."""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.accounts.deactivation import deactivate_user
from apps.notifications.models import Notification, NotificationType
from tests.api.helpers import bearer
from tests.factories import (
    make_course,
    make_notification,
    make_student,
    make_teacher,
)

pytestmark = pytest.mark.django_db

COMMAND = "send_notification_emails"
REQUESTS_URL = "/api/orientacao/solicitacoes"


def request_advising(client):
    course = make_course()
    student = make_student(course=course, full_name="Ana Souza")
    teacher = make_teacher(courses=[course])
    response = client.post(
        REQUESTS_URL,
        data={"teacher_id": teacher.user_id},
        content_type="application/json",
        headers=bearer(student.user),
    )
    assert response.status_code == 201
    return student, teacher


def test_a_new_advising_request_is_e_mailed_to_the_teacher_with_the_notification_text(
    client, mailoutbox
):
    student, teacher = request_advising(client)

    call_command(COMMAND)

    assert len(mailoutbox) == 1
    message = mailoutbox[0]
    assert message.to == [teacher.user.email]
    assert message.subject == "Nova solicitação de orientação"
    assert "Ana Souza enviou uma solicitação de orientação." in message.body
    assert "http://localhost:3000" in message.body
    assert Notification.objects.get().email_sent_at is not None


def test_running_the_command_again_does_not_send_the_same_e_mail_twice(client, mailoutbox):
    request_advising(client)

    call_command(COMMAND)
    call_command(COMMAND)

    assert len(mailoutbox) == 1


def test_the_e_mail_shows_neither_the_address_nor_the_registration_of_anyone_else(
    client, mailoutbox
):
    student, teacher = request_advising(client)

    call_command(COMMAND)

    body = mailoutbox[0].body
    assert student.user.email not in body
    assert student.registration_number not in body
    assert teacher.registration_number not in body


def test_the_pending_notifications_are_sent_oldest_first_each_to_its_own_recipient(mailoutbox):
    first, second = make_student().user, make_teacher().user
    make_notification(first, NotificationType.ADVISING_REQUEST_UNANSWERED, payload=UNANSWERED)
    make_notification(second, NotificationType.ADVISING_REQUEST_RECEIVED, payload=RECEIVED)

    call_command(COMMAND)

    assert [message.to for message in mailoutbox] == [[first.email], [second.email]]


RECEIVED = {"student_name": "Ana Souza"}
UNANSWERED = {"teacher_name": "Carlos Lima"}
SCHEDULED = "2026-12-01T14:30:00"

# One realistic payload and the exact text each of the 13 types is e-mailed with (RF54).
EVERY_TYPE = {
    NotificationType.ADVISING_REQUEST_RECEIVED: (
        RECEIVED,
        "Ana Souza enviou uma solicitação de orientação.",
    ),
    NotificationType.ADVISING_REQUEST_ANSWERED: (
        {"teacher_name": "Carlos Lima", "decision": "accepted"},
        "Carlos Lima aceitou a sua solicitação de orientação.",
    ),
    NotificationType.ADVISING_REQUEST_UNANSWERED: (
        UNANSWERED,
        "Carlos Lima não respondeu a sua solicitação de orientação em 7 dias. "
        "Você já pode enviar uma nova.",
    ),
    NotificationType.ACTIVITY_CREATED: (
        {"teacher_name": "Carlos Lima", "activity_title": "Capítulo 2", "deadline": "2026-12-15"},
        'Carlos Lima criou a atividade "Capítulo 2" para você, com prazo em 15/12/2026.',
    ),
    NotificationType.ACTIVITY_COMMENTED: (
        {"author_name": "Carlos Lima", "activity_title": "Capítulo 2"},
        'Carlos Lima comentou na atividade "Capítulo 2".',
    ),
    NotificationType.ACTIVITY_SUBMITTED: (
        {"student_name": "Ana Souza", "activity_title": "Capítulo 2"},
        'Ana Souza entregou arquivos da atividade "Capítulo 2".',
    ),
    NotificationType.ACTIVITY_AWAITING_REVIEW: (
        {"student_name": "Ana Souza", "activity_title": "Capítulo 2"},
        'Ana Souza enviou a atividade "Capítulo 2" para revisão.',
    ),
    NotificationType.ACTIVITY_REVIEW_RESULT: (
        {"teacher_name": "Carlos Lima", "decision": "accepted", "activity_title": "Capítulo 2"},
        'Carlos Lima aceitou a atividade "Capítulo 2".',
    ),
    NotificationType.MEETING_REQUESTED: (
        {"author_type": "teacher", "author_name": "Carlos Lima", "scheduled_at": SCHEDULED},
        "Carlos Lima marcou uma reunião com você para 01/12/2026 às 14:30.",
    ),
    NotificationType.MEETING_ANSWERED: (
        {"responder_name": "Carlos Lima", "decision": "accepted", "scheduled_at": SCHEDULED},
        "Carlos Lima aceitou a reunião de 01/12/2026 às 14:30.",
    ),
    NotificationType.ACTIVITY_CHANGED: (
        {
            "teacher_name": "Carlos Lima",
            "activity_title": "Capítulo 2",
            "changes": [{"field": "deadline", "old": "2026-12-15", "new": "2026-12-20"}],
        },
        'Carlos Lima alterou a atividade "Capítulo 2": prazo de 15/12/2026 para 20/12/2026.',
    ),
    NotificationType.ACTIVITY_DELETED: (
        {"teacher_name": "Carlos Lima", "activity_title": "Capítulo 2"},
        'Carlos Lima excluiu a atividade "Capítulo 2".',
    ),
    NotificationType.ADVISING_ENDED: (
        {"teacher_name": "Carlos Lima", "archived_activities": 1, "cancelled_meetings": 0},
        "Carlos Lima encerrou a sua orientação. 1 atividade em aberto foi arquivada. "
        "Nenhuma reunião pendente ou aceita foi cancelada.",
    ),
}


def test_every_notification_type_has_a_case_below():
    assert set(EVERY_TYPE) == set(NotificationType)


@pytest.mark.parametrize("notification_type", list(NotificationType))
def test_each_type_is_e_mailed_once_to_its_recipient_with_its_label_and_text(
    notification_type, mailoutbox
):
    recipient = make_student().user
    payload, text = EVERY_TYPE[notification_type]
    make_notification(recipient, notification_type, payload=payload)

    call_command(COMMAND)

    (message,) = mailoutbox
    assert message.to == [recipient.email]
    assert message.subject == notification_type.label
    assert text in message.body


FAILING_BACKEND = "tests.api.failing_email_backend.FailingEmailBackend"
FAILING_FOR_ONE = "tests.api.failing_email_backend.FailingForOneAddressEmailBackend"


def pending_for(user, type=NotificationType.ADVISING_REQUEST_RECEIVED):
    return make_notification(user, type, payload=RECEIVED)


def reload(notification):
    return Notification.objects.get(pk=notification.pk)


@override_settings(EMAIL_BACKEND=FAILING_BACKEND)
def test_a_failed_send_leaves_the_notification_pending_counts_the_attempt_and_reports_it():
    notification = pending_for(make_teacher().user)

    with pytest.raises(CommandError, match=str(notification.pk)):
        call_command(COMMAND)

    notification = reload(notification)
    assert notification.email_sent_at is None
    assert notification.email_attempts == 1


@override_settings(EMAIL_BACKEND=FAILING_FOR_ONE)
def test_a_notification_that_fails_does_not_block_the_ones_behind_it(mailoutbox):
    rejected = make_teacher().user
    rejected.email = "falha@example.com"
    rejected.save(update_fields=["email"])
    failing = pending_for(rejected)
    healthy = pending_for(make_teacher().user)

    with pytest.raises(CommandError):
        call_command(COMMAND)

    assert [message.to for message in mailoutbox] == [[healthy.recipient.email]]
    assert reload(healthy).email_sent_at is not None
    assert reload(failing).email_attempts == 1


@override_settings(EMAIL_BACKEND=FAILING_BACKEND)
def test_after_five_failures_it_gives_up_with_a_log_and_never_tries_again(caplog):
    notification = pending_for(make_teacher().user)

    for _ in range(5):
        with pytest.raises(CommandError):
            call_command(COMMAND)
    call_command(COMMAND)

    notification = reload(notification)
    assert notification.email_attempts == 5
    assert notification.email_sent_at is None
    assert any(
        record.levelname == "ERROR" and "gave up" in record.getMessage()
        for record in caplog.records
    )


def test_an_inactive_account_is_not_e_mailed_and_it_is_not_a_failure(mailoutbox):
    teacher = make_teacher(is_active=False)
    notification = pending_for(teacher.user)

    call_command(COMMAND)

    assert mailoutbox == []
    notification = reload(notification)
    assert notification.email_sent_at is not None
    assert notification.email_attempts == 0


def test_an_account_deactivated_after_the_notification_was_created_is_not_e_mailed(mailoutbox):
    teacher = make_teacher()
    notification = pending_for(teacher.user)
    deactivate_user(teacher.user)

    call_command(COMMAND)

    assert mailoutbox == []
    assert reload(notification).email_sent_at is not None
