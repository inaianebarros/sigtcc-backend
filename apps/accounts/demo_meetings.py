"""The topic, the meetings and the read notifications of the active advising of student 5.

Made with the real services, so each step notifies the other party. Every meeting is skipped if
its description already exists, and the notifications are only marked as read, so running again
adds nothing. Two situations need the clock moved: a held meeting (accepted, in the past) and an
expired one (pending, in the past). A cancellation by the system needs an advising to end: see
the ended advising of student 7.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, time, timedelta

from django.db import transaction
from django.utils import timezone

from apps.advising.models import Advising, AnswerAction
from apps.advising.thesis import write_topic
from apps.meetings.models import Meeting
from apps.meetings.services import answer_meeting, cancel_meeting, create_meeting
from apps.notifications.models import Notification, NotificationType
from apps.notifications.services import mark_as_read

from .demo_scenarios import ADVISOR, COMPLETE_STUDENT
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


def _day(in_days: int) -> date:
    return timezone.localdate() + timedelta(days=in_days)


def _teacher_proposes(context: Context, description: str, in_days: int, at_hour: int) -> Meeting:
    return create_meeting(
        context.teacher.user,
        _day(in_days),
        time(at_hour, 0),
        time(at_hour + 1, 0),
        description,
        context.student.user_id,
    )


def _student_requests(context: Context, description: str, in_days: int, at_hour: int) -> Meeting:
    return create_meeting(
        context.student.user, _day(in_days), time(at_hour, 0), time(at_hour + 1, 0), description
    )


def _accept(user: User, meeting: Meeting) -> None:
    answer_meeting(user, meeting.pk, AnswerAction.ACCEPT, None)


def _pending_by_the_student(context: Context, description: str) -> None:
    _student_requests(context, description, in_days=3, at_hour=10)


def _pending_by_the_teacher(context: Context, description: str) -> None:
    _teacher_proposes(context, description, in_days=6, at_hour=14)


def _accepted(context: Context, description: str) -> None:
    meeting = _teacher_proposes(context, description, in_days=2, at_hour=9)
    _accept(context.student.user, meeting)


def _rejected(context: Context, description: str) -> None:
    meeting = _student_requests(context, description, in_days=8, at_hour=15)
    answer_meeting(
        context.teacher.user,
        meeting.pk,
        AnswerAction.REJECT,
        "Estarei em viagem nessa data. Proponha outra data, por favor.",
    )


def _cancelled_by_the_teacher(context: Context, description: str) -> None:
    meeting = _teacher_proposes(context, description, in_days=4, at_hour=11)
    _accept(context.student.user, meeting)
    cancel_meeting(context.teacher.user, meeting.pk, "Tenho uma banca nesse horário; remarcamos.")


def _cancelled_by_the_student(context: Context, description: str) -> None:
    meeting = _student_requests(context, description, in_days=5, at_hour=16)
    _accept(context.teacher.user, meeting)
    cancel_meeting(context.student.user, meeting.pk, "Vou viajar nesse dia, desculpe o aviso.")


def _move_to_the_past(meeting: Meeting, ago: timedelta) -> None:
    start = timezone.now() - ago
    Meeting.objects.filter(pk=meeting.pk).update(
        scheduled_at=start, ends_at=start + timedelta(hours=1)
    )


def _held(context: Context, description: str) -> None:
    meeting = _teacher_proposes(context, description, in_days=2, at_hour=10)
    _accept(context.student.user, meeting)
    _move_to_the_past(meeting, HELD_AGO)


def _expired(context: Context, description: str) -> None:
    meeting = _student_requests(context, description, in_days=2, at_hour=17)
    _move_to_the_past(meeting, EXPIRED_AGO)


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
