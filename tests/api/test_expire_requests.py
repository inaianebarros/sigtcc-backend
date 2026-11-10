"""The command that marks the overdue advising requests as unanswered (RF44, RF45, RN20, #60)."""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.advising import expiration
from apps.advising.deadlines import ANSWER_DEADLINE
from apps.advising.models import AdvisingRequest, AdvisingRequestStatus
from apps.notifications.messages import message_for
from apps.notifications.models import Notification, NotificationType
from tests.api.helpers import bearer, inbox, post_json
from tests.factories import make_advising_request, make_course, make_student, make_teacher

pytestmark = pytest.mark.django_db

COMMAND = "expire_advising_requests"
PENDING = AdvisingRequestStatus.PENDING
UNANSWERED = AdvisingRequestStatus.UNANSWERED
EXPECTED_MESSAGE = (
    "Paulo Professor não respondeu a sua solicitação de orientação em 7 dias. "
    "Você já pode enviar uma nova."
)


def overdue_request(days_ago=8, status=PENDING, teacher=None, student=None):
    advising_request = make_advising_request(student=student, teacher=teacher)
    AdvisingRequest.objects.filter(pk=advising_request.pk).update(
        requested_at=timezone.now() - timedelta(days=days_ago), status=status
    )
    return advising_request


def status_of(advising_request):
    return AdvisingRequest.objects.get(pk=advising_request.pk).status


def unanswered_notifications(student=None):
    queryset = Notification.objects.filter(type=NotificationType.ADVISING_REQUEST_UNANSWERED)
    return queryset.filter(recipient=student.user) if student else queryset


def run_command():
    out = StringIO()
    call_command(COMMAND, stdout=out)
    return out.getvalue()


def test_the_command_marks_the_overdue_pending_requests_as_unanswered():
    first, second = overdue_request(days_ago=8), overdue_request(days_ago=30)

    run_command()

    assert status_of(first) == UNANSWERED
    assert status_of(second) == UNANSWERED


def test_the_command_leaves_every_other_request_alone():
    recent = overdue_request(days_ago=6)
    accepted = overdue_request(days_ago=20, status=AdvisingRequestStatus.ACCEPTED)
    rejected = overdue_request(days_ago=20, status=AdvisingRequestStatus.REJECTED)
    already = overdue_request(days_ago=20, status=UNANSWERED)

    run_command()

    assert status_of(recent) == PENDING
    assert status_of(accepted) == AdvisingRequestStatus.ACCEPTED
    assert status_of(rejected) == AdvisingRequestStatus.REJECTED
    assert status_of(already) == UNANSWERED
    assert unanswered_notifications().count() == 0


def test_the_exact_boundary_of_seven_days_follows_the_reading_rule():
    """A request is overdue from the instant its seven days are over, as `effective_status` reads."""
    now = timezone.now()
    on_the_boundary = make_advising_request()
    a_second_short = make_advising_request()
    AdvisingRequest.objects.filter(pk=on_the_boundary.pk).update(requested_at=now - ANSWER_DEADLINE)
    AdvisingRequest.objects.filter(pk=a_second_short.pk).update(
        requested_at=now - ANSWER_DEADLINE + timedelta(seconds=1)
    )

    with patch("apps.advising.deadlines.timezone.now", return_value=now):
        run_command()

    assert status_of(on_the_boundary) == UNANSWERED
    assert status_of(a_second_short) == PENDING


def test_each_marked_request_notifies_its_student_once_with_the_teacher_name(client):
    teacher = make_teacher(full_name="Paulo Professor")
    student = make_student()
    advising_request = overdue_request(teacher=teacher, student=student)

    run_command()

    notifications = unanswered_notifications(student)
    assert notifications.count() == 1
    notification = notifications.get()
    assert notification.reference_type == "advising_request"
    assert notification.reference_id == advising_request.pk
    assert notification.payload == {"teacher_name": "Paulo Professor"}
    assert not notification.is_read
    listed = inbox(client, student.user)
    assert [item["message"] for item in listed] == [EXPECTED_MESSAGE]
    assert unanswered_notifications(make_student()).count() == 0


def test_the_teacher_is_not_notified():
    teacher = make_teacher()
    overdue_request(teacher=teacher)

    run_command()

    assert Notification.objects.filter(recipient=teacher.user).count() == 0


def test_running_the_command_again_changes_and_notifies_nothing_more():
    student = make_student()
    overdue_request(student=student)
    run_command()

    second = run_command()

    assert unanswered_notifications(student).count() == 1
    assert "Nenhuma" in second


def test_the_command_reports_how_many_requests_it_marked():
    overdue_request()
    overdue_request()

    assert "2" in run_command()


def test_the_student_stays_free_to_ask_again_after_the_expiration(client):
    student = make_student()
    overdue_request(student=student)
    run_command()

    response = client.get("/api/orientacao/minha-solicitacao", headers=bearer(student.user))

    body = response.json()
    assert body["request"]["status"] == "unanswered"
    assert body["can_request"] is True
    assert body["blocked_reason"] is None


def test_the_lazy_marking_when_the_student_asks_again_also_notifies_once(client):
    course = make_course()
    teacher = make_teacher(courses=[course], full_name="Paulo Professor")
    student = make_student(course=course)
    old = overdue_request(teacher=teacher, student=student)

    response = post_json(
        client,
        "/api/orientacao/solicitacoes",
        {"teacher_id": teacher.user_id},
        headers=bearer(student.user),
    )
    run_command()

    assert response.status_code == 201
    assert status_of(old) == UNANSWERED
    notifications = unanswered_notifications(student)
    assert notifications.count() == 1
    assert notifications.get().reference_id == old.pk


def test_a_failure_undoes_the_request_it_hit_and_the_others_are_still_marked():
    student = make_student()
    first, second, third = (overdue_request(student=make_student()) for _ in range(3))
    real_notify = expiration.notify

    def fail_for_the_second(recipient, notification_type, **kwargs):
        if kwargs["reference_id"] == second.pk:
            raise RuntimeError("falha ao notificar")
        return real_notify(recipient, notification_type, **kwargs)

    with (
        patch("apps.advising.expiration.notify", side_effect=fail_for_the_second),
        pytest.raises(CommandError, match="1"),
    ):
        call_command(COMMAND, stdout=StringIO())

    assert status_of(first) == UNANSWERED
    assert status_of(third) == UNANSWERED
    assert status_of(second) == PENDING
    assert unanswered_notifications().filter(reference_id=second.pk).count() == 0
    assert unanswered_notifications().count() == 2
    assert student.pk  # the first loop variable is not used on purpose

    run_command()

    assert status_of(second) == UNANSWERED
    assert unanswered_notifications().count() == 3


def test_the_message_is_the_one_of_the_brief_and_falls_back_without_the_teacher_name():
    text = message_for(NotificationType.ADVISING_REQUEST_UNANSWERED, {"teacher_name": "Ana"})

    assert text == (
        "Ana não respondeu a sua solicitação de orientação em 7 dias. Você já pode enviar uma nova."
    )
    assert message_for(NotificationType.ADVISING_REQUEST_UNANSWERED, {}) == (
        "Solicitação de orientação não respondida"
    )


def test_the_expiration_service_is_not_listed_as_a_cron_placeholder_anymore():
    compose = expiration.__file__.rsplit("apps", 1)[0] + "compose.yaml"
    text = open(compose, encoding="utf-8").read()

    assert "\n  expiration-cron:" in text
    assert "# expiration-cron:" not in text
    assert "expire_advising_requests" in text
    assert "sleep 3600" in text
