import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
from ninja.errors import HttpError

from apps.accounts.models import Student, Teacher, User, UserType
from apps.advising.linked_student_profile import STUDENT_NOT_FOUND_MESSAGE
from apps.advising.models import Advising, AdvisingStatus, AnswerAction
from config.errors import ConflictError, FieldValidationError

from .models import Meeting, MeetingStatus

PAST_SCHEDULE_MESSAGE = "A data e a hora devem ser posteriores ao momento atual."
NO_ACTIVE_ADVISING_MESSAGE = "Você precisa ter um orientador ativo para solicitar uma reunião."
STUDENT_ID_NOT_ALLOWED_MESSAGE = "O aluno não informa o aluno da reunião."
STUDENT_ID_REQUIRED_MESSAGE = "Informe o aluno."
MEETING_NOT_FOUND_MESSAGE = "Reunião não encontrada."
ALREADY_ANSWERED_MESSAGE = "Esta reunião já foi respondida."
CANCELLED_MESSAGE = "Esta reunião foi cancelada."
TIME_PASSED_MESSAGE = "O horário desta reunião já passou."
REASON_REQUIRED_MESSAGE = "Informe o motivo da recusa."
REASON_NOT_ALLOWED_MESSAGE = "O aceite não tem motivo."


def to_scheduled_at(day: datetime.date, at: datetime.time) -> datetime.datetime:
    """The date and the time as given in the project time zone, strictly after now (RF17)."""
    moment = datetime.datetime.combine(day, at, tzinfo=ZoneInfo(settings.TIME_ZONE))
    if moment <= timezone.now():
        raise FieldValidationError({"date": PAST_SCHEDULE_MESSAGE})
    return moment


def _lock_active_advising_of_student(student: Student) -> Advising:
    advising = (
        Advising.objects.select_for_update()
        .filter(student=student, status=AdvisingStatus.ACTIVE)
        .first()
    )
    if advising is None:
        raise ConflictError(NO_ACTIVE_ADVISING_MESSAGE)
    return advising


def _lock_active_advising_with_student(teacher: Teacher, student_id: int) -> Advising:
    advising = (
        Advising.objects.select_for_update()
        .filter(teacher=teacher, student__user_id=student_id, status=AdvisingStatus.ACTIVE)
        .first()
    )
    if advising is None:
        raise HttpError(404, STUDENT_NOT_FOUND_MESSAGE)
    return advising


@transaction.atomic
def create_meeting(
    author: User,
    day: datetime.date,
    at: datetime.time,
    description: str,
    student_id: int | None = None,
) -> Meeting:
    """A Student asks their Advisor, or a Teacher asks an active Advisee, for a Meeting (RF17, RF18).

    The advising row is locked by the same query that checks it is active, so an advising that
    ends meanwhile gets the same answer as having none (409 for the Student, 404 for the Teacher,
    who must not learn about a Student that is not theirs) and nothing is created (ADR 0004).
    """
    is_student = author.type == UserType.STUDENT
    if is_student and student_id is not None:
        raise FieldValidationError({"student_id": STUDENT_ID_NOT_ALLOWED_MESSAGE})
    if not is_student and student_id is None:
        raise FieldValidationError({"student_id": STUDENT_ID_REQUIRED_MESSAGE})
    scheduled_at = to_scheduled_at(day, at)
    if is_student:
        advising = _lock_active_advising_of_student(author.student)
    else:
        advising = _lock_active_advising_with_student(author.teacher, student_id)
    return Meeting.objects.create(
        advising=advising, author=author, scheduled_at=scheduled_at, description=description
    )


def _reason_for(action: AnswerAction, reason: str | None) -> str:
    """The text stored with the answer: the rejection needs one (RN11) and the acceptance has none."""
    if action == AnswerAction.ACCEPT:
        if reason is not None:
            raise FieldValidationError({"reason": REASON_NOT_ALLOWED_MESSAGE})
        return ""
    if reason is None:
        raise FieldValidationError({"reason": REASON_REQUIRED_MESSAGE})
    return reason


def _lock_advising_of_counterpart(user: User, meeting_id: int) -> None:
    """Locks the active advising whose other party is the user, for a meeting the user did not author.

    One query both checks and locks, so an advising that ends meanwhile gets the same 404 as a
    meeting the user cannot answer (ADR 0004).
    """
    meeting_of_the_other_party = Meeting.objects.filter(
        pk=meeting_id, advising=OuterRef("pk")
    ).exclude(author=user)
    locked = (
        Advising.objects.select_for_update()
        .filter(status=AdvisingStatus.ACTIVE)
        .filter(Q(student__user=user) | Q(teacher__user=user))
        .filter(Exists(meeting_of_the_other_party))
        .values_list("pk", flat=True)
        .first()
    )
    if locked is None:
        raise HttpError(404, MEETING_NOT_FOUND_MESSAGE)


def _ensure_can_be_answered(meeting: Meeting) -> None:
    if meeting.status == MeetingStatus.CANCELLED:
        raise ConflictError(CANCELLED_MESSAGE)
    if meeting.status != MeetingStatus.PENDING:
        raise ConflictError(ALREADY_ANSWERED_MESSAGE)
    if meeting.scheduled_at <= timezone.now():
        raise ConflictError(TIME_PASSED_MESSAGE)


@transaction.atomic
def answer_meeting(
    user: User, meeting_id: int, action: AnswerAction, reason: str | None
) -> Meeting:
    """The other party of the advising accepts or rejects a pending meeting (RF20, RN11).

    The author never answers their own request, and the answer is final.
    """
    rejection_reason = _reason_for(action, reason)
    _lock_advising_of_counterpart(user, meeting_id)
    meeting = Meeting.objects.select_related("author").get(pk=meeting_id)
    _ensure_can_be_answered(meeting)
    if action == AnswerAction.ACCEPT:
        meeting.status = MeetingStatus.ACCEPTED
    else:
        meeting.status = MeetingStatus.REJECTED
        meeting.rejection_reason = rejection_reason
    meeting.save(update_fields=["status", "rejection_reason"])
    return meeting
