import datetime
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
from ninja.errors import HttpError

from apps.accounts.models import Student, Teacher, User, UserType
from apps.advising.linked_student_profile import STUDENT_NOT_FOUND_MESSAGE
from apps.advising.models import Advising, AdvisingStatus, AnswerAction
from config.changes import FieldChange
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
NOT_EDITABLE_MESSAGE = "Esta reunião não pode mais ser editada."
REJECTED_NOT_CANCELLABLE_MESSAGE = "Esta reunião foi recusada e não pode ser cancelada."
ALREADY_CANCELLED_MESSAGE = "Esta reunião já foi cancelada."


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


def _locked_meeting_of_party(user: User, meeting_id: int) -> Meeting:
    """The meeting, with the advising row locked, for a user who is a party of its active advising.

    One query both checks the user is the student or the teacher of an advising that is still
    active and locks it, so an advising that ends meanwhile gets the same 404 as a meeting the
    user cannot reach (ADR 0004). Who may do what with it is up to the caller, with the same 404.
    """
    meeting_of_the_advising = Meeting.objects.filter(pk=meeting_id, advising=OuterRef("pk"))
    locked = (
        Advising.objects.select_for_update()
        .filter(status=AdvisingStatus.ACTIVE)
        .filter(Q(student__user=user) | Q(teacher__user=user))
        .filter(Exists(meeting_of_the_advising))
        .values_list("pk", flat=True)
        .first()
    )
    if locked is None:
        raise HttpError(404, MEETING_NOT_FOUND_MESSAGE)
    return Meeting.objects.select_related("author").get(pk=meeting_id)


def _ensure_not_past(meeting: Meeting) -> None:
    if meeting.scheduled_at <= timezone.now():
        raise ConflictError(TIME_PASSED_MESSAGE)


def _ensure_can_be_answered(meeting: Meeting) -> None:
    if meeting.status == MeetingStatus.CANCELLED:
        raise ConflictError(CANCELLED_MESSAGE)
    if meeting.status != MeetingStatus.PENDING:
        raise ConflictError(ALREADY_ANSWERED_MESSAGE)
    _ensure_not_past(meeting)


@transaction.atomic
def answer_meeting(
    user: User, meeting_id: int, action: AnswerAction, reason: str | None
) -> Meeting:
    """The other party of the advising accepts or rejects a pending meeting (RF20, RN11).

    The author never answers their own request, and the answer is final.
    """
    rejection_reason = _reason_for(action, reason)
    meeting = _locked_meeting_of_party(user, meeting_id)
    if meeting.author_id == user.id:
        raise HttpError(404, MEETING_NOT_FOUND_MESSAGE)
    _ensure_can_be_answered(meeting)
    if action == AnswerAction.ACCEPT:
        meeting.status = MeetingStatus.ACCEPTED
    else:
        meeting.status = MeetingStatus.REJECTED
        meeting.rejection_reason = rejection_reason
    meeting.save(update_fields=["status", "rejection_reason"])
    return meeting


@dataclass(frozen=True)
class MeetingEdit:
    meeting: Meeting
    changes: dict[str, FieldChange]


@transaction.atomic
def edit_meeting(
    user: User,
    meeting_id: int,
    day: datetime.date | None = None,
    at: datetime.time | None = None,
    description: str | None = None,
) -> MeetingEdit:
    """The author changes the date, the time or the description of a meeting (RF21, RN09, RN39).

    Only the author edits, and only a meeting that has not happened and was not rejected or
    cancelled. A new date or time must be in the future, and only when it changed. Changing the
    date or the time of an accepted meeting sends it back to pending (RN39). An edit that
    changes nothing writes nothing and reports no change.
    """
    meeting = _locked_meeting_of_party(user, meeting_id)
    if meeting.author_id != user.id:
        raise HttpError(404, MEETING_NOT_FOUND_MESSAGE)
    if meeting.status in (MeetingStatus.REJECTED, MeetingStatus.CANCELLED):
        raise ConflictError(NOT_EDITABLE_MESSAGE)
    _ensure_not_past(meeting)
    changes: dict[str, FieldChange] = {}
    if day is not None or at is not None:
        current = meeting.scheduled_at.astimezone(ZoneInfo(settings.TIME_ZONE))
        moved = datetime.datetime.combine(
            day or current.date(), at or current.time(), tzinfo=current.tzinfo
        )
        if moved != meeting.scheduled_at:
            if moved <= timezone.now():
                raise FieldValidationError({"date": PAST_SCHEDULE_MESSAGE})
            changes["scheduled_at"] = FieldChange(old=meeting.scheduled_at, new=moved)
    if description is not None and description != meeting.description:
        changes["description"] = FieldChange(old=meeting.description, new=description)
    if changes:
        for field, change in changes.items():
            setattr(meeting, field, change.new)
        if "scheduled_at" in changes and meeting.status == MeetingStatus.ACCEPTED:
            changes["status"] = FieldChange(old=meeting.status, new=MeetingStatus.PENDING)
            meeting.status = MeetingStatus.PENDING
        meeting.save(update_fields=list(changes))
    return MeetingEdit(meeting=meeting, changes=changes)


@transaction.atomic
def cancel_meeting(user: User, meeting_id: int, reason: str) -> Meeting:
    """The author cancels a pending meeting and either party cancels an accepted one (RF22, RN10).

    The reason is required and the cancellation is final. The status and the reason are stored
    together, as the constraints of the table require.
    """
    meeting = _locked_meeting_of_party(user, meeting_id)
    if meeting.status == MeetingStatus.PENDING and meeting.author_id != user.id:
        raise HttpError(404, MEETING_NOT_FOUND_MESSAGE)
    if meeting.status == MeetingStatus.REJECTED:
        raise ConflictError(REJECTED_NOT_CANCELLABLE_MESSAGE)
    if meeting.status == MeetingStatus.CANCELLED:
        raise ConflictError(ALREADY_CANCELLED_MESSAGE)
    _ensure_not_past(meeting)
    meeting.status = MeetingStatus.CANCELLED
    meeting.cancellation_reason = reason
    meeting.save(update_fields=["status", "cancellation_reason"])
    return meeting
