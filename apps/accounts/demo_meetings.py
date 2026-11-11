"""The topic, the meetings and the read notifications of the active advising of student 5.

Made with the real services, so each step notifies the other party. Every meeting is skipped if
its description already exists, and the notifications are only marked as read, so running again
adds nothing. Two situations need the clock moved: a held meeting (accepted, in the past) and an
expired one (pending, in the past). The system cancellation is only possible when an advising ends,
so it is in the ended advising of student 7, not here.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import time, timedelta

from django.db import transaction
from django.utils import timezone

from apps.advising.models import Advising, AnswerAction
from apps.advising.thesis import write_topic
from apps.meetings.models import Meeting
from apps.meetings.services import answer_meeting, cancel_meeting, create_meeting
from apps.notifications.models import Notification, NotificationType
from apps.notifications.services import mark_as_read

from .demo_activities import ADVISOR, COMPLETE_STUDENT
from .demo_seed import demo_email
from .models import Student, Teacher, User

THESIS_TOPIC = "Modelagem de dados para um sistema de gestão de trabalhos de conclusão de curso"
HELD_AGO = timedelta(days=12)
EXPIRED_AGO = timedelta(days=2)

STUDENT_NOTIFICATIONS_READ = (
    NotificationType.ADVISING_REQUEST_ANSWERED,
    NotificationType.ACTIVITY_CREATED,
    NotificationType.ACTIVITY_REVIEW_RESULT,
)
TEACHER_NOTIFICATIONS_READ = (
    NotificationType.ADVISING_REQUEST_RECEIVED,
    NotificationType.ACTIVITY_SUBMITTED,
)


@dataclass(frozen=True)
class Context:
    teacher: Teacher
    student: Student
    advising: Advising


@dataclass(frozen=True)
class Plan:
    description: str
    steps: Callable[[Context, str], None]


def _day(days: int):
    return timezone.localdate() + timedelta(days=days)


def _teacher_proposes(context: Context, description: str, days: int, hour: int) -> Meeting:
    return create_meeting(
        context.teacher.user, _day(days), time(hour, 0), description, context.student.user_id
    )


def _student_requests(context: Context, description: str, days: int, hour: int) -> Meeting:
    return create_meeting(context.student.user, _day(days), time(hour, 0), description)


def _pending_by_the_student(context: Context, description: str) -> None:
    _student_requests(context, description, 3, 10)


def _pending_by_the_teacher(context: Context, description: str) -> None:
    _teacher_proposes(context, description, 6, 14)


def _accepted(context: Context, description: str) -> None:
    meeting = _teacher_proposes(context, description, 2, 9)
    answer_meeting(context.student.user, meeting.pk, AnswerAction.ACCEPT, None)


def _rejected(context: Context, description: str) -> None:
    meeting = _student_requests(context, description, 8, 15)
    answer_meeting(
        context.teacher.user,
        meeting.pk,
        AnswerAction.REJECT,
        "Estarei em viagem nessa data. Proponha outra data, por favor.",
    )


def _cancelled_by_the_teacher(context: Context, description: str) -> None:
    meeting = _teacher_proposes(context, description, 4, 11)
    answer_meeting(context.student.user, meeting.pk, AnswerAction.ACCEPT, None)
    cancel_meeting(context.teacher.user, meeting.pk, "Tenho uma banca nesse horário; remarcamos.")


def _cancelled_by_the_student(context: Context, description: str) -> None:
    meeting = _student_requests(context, description, 5, 16)
    answer_meeting(context.teacher.user, meeting.pk, AnswerAction.ACCEPT, None)
    cancel_meeting(context.student.user, meeting.pk, "Vou viajar nesse dia, desculpe o aviso.")


def _held(context: Context, description: str) -> None:
    meeting = _teacher_proposes(context, description, 2, 10)
    answer_meeting(context.student.user, meeting.pk, AnswerAction.ACCEPT, None)
    Meeting.objects.filter(pk=meeting.pk).update(scheduled_at=timezone.now() - HELD_AGO)


def _expired(context: Context, description: str) -> None:
    meeting = _student_requests(context, description, 2, 17)
    Meeting.objects.filter(pk=meeting.pk).update(scheduled_at=timezone.now() - EXPIRED_AGO)


PLANS = (
    Plan("Dúvidas sobre a metodologia", _pending_by_the_student),
    Plan("Alinhar o cronograma", _pending_by_the_teacher),
    Plan("Revisão do capítulo 1", _accepted),
    Plan("Reunião extra de orientação", _rejected),
    Plan("Ajuste do plano de trabalho", _cancelled_by_the_teacher),
    Plan("Conversa sobre as referências", _cancelled_by_the_student),
    Plan("Primeira orientação do semestre", _held),
    Plan("Reunião sem resposta", _expired),
)


def _mark_read(user: User, kinds: tuple[str, ...]) -> None:
    unread = Notification.objects.filter(recipient=user, is_read=False, type__in=kinds)
    for notification_id in list(unread.values_list("pk", flat=True)):
        mark_as_read(user, notification_id)


@transaction.atomic
def seed_meeting_scenarios() -> int:
    """Gives student 5 a thesis topic, meetings in every situation and read notifications."""
    teacher = User.objects.get(email=demo_email(ADVISOR)).teacher
    student = User.objects.get(email=demo_email(COMPLETE_STUDENT)).student
    advising = Advising.objects.get(student=student)
    context = Context(teacher, student, advising)

    if not hasattr(advising, "thesis_topic"):
        write_topic(advising, THESIS_TOPIC)
    created = 0
    for plan in PLANS:
        if Meeting.objects.filter(advising=advising, description=plan.description).exists():
            continue
        plan.steps(context, plan.description)
        created += 1
    _mark_read(student.user, STUDENT_NOTIFICATIONS_READ)
    _mark_read(teacher.user, TEACHER_NOTIFICATIONS_READ)
    return created
