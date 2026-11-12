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
from apps.notifications.models import MEETING_REFERENCE, NotificationType
from apps.notifications.services import notify
from config.changes import FieldChange
from config.errors import ConflictError, FieldValidationError

from .models import Meeting, MeetingStatus
from .situations import is_held

PAST_SCHEDULE_MESSAGE = "A data e a hora devem ser posteriores ao momento atual."
END_NOT_AFTER_START_MESSAGE = "A hora de término deve ser depois da hora de início."
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
ALREADY_HELD_MESSAGE = "Esta reunião já foi marcada como realizada."
NOT_HELD_MESSAGE = "Só uma reunião realizada pode ter apontamentos."
NOT_ACCEPTED_MESSAGE = "Só uma reunião aceita pode ser marcada como realizada."


def _local_iso(moment: datetime.datetime) -> str:
    """A meeting instant as kept in a notification payload: ISO text in the institution's time zone."""
    return timezone.localtime(moment).isoformat()


def _answer_payload(
    responder: User, meeting: Meeting, action: AnswerAction, rejection_reason: str
) -> dict:
    """The snapshot of an answer: who answered, what, when the meeting is, and why if rejected."""
    payload = {
        "responder_name": responder.full_name,
        "decision": action.decision,
        "scheduled_at": _local_iso(meeting.scheduled_at),
    }
    if action == AnswerAction.REJECT:
        payload["reason"] = rejection_reason
    return payload


def _at(day: datetime.date, at: datetime.time) -> datetime.datetime:
    return datetime.datetime.combine(day, at, tzinfo=ZoneInfo(settings.TIME_ZONE))


def to_interval(
    day: datetime.date, at: datetime.time, end_at: datetime.time
) -> tuple[datetime.datetime, datetime.datetime]:
    """The start and the end as given in the project time zone, on the same day (RF17, RN45).

    The start must be strictly after now, and the end after the start. Both share the date, so a
    meeting never ends on another day.
    """
    start = _at(day, at)
    if start <= timezone.now():
        raise FieldValidationError({"date": PAST_SCHEDULE_MESSAGE})
    if end_at <= at:
        raise FieldValidationError({"end_time": END_NOT_AFTER_START_MESSAGE})
    return start, _at(day, end_at)


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
    end_at: datetime.time,
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
    scheduled_at, ends_at = to_interval(day, at, end_at)
    if is_student:
        advising = _lock_active_advising_of_student(author.student)
    else:
        advising = _lock_active_advising_with_student(author.teacher, student_id)
    meeting = Meeting.objects.create(
        advising=advising,
        author=author,
        scheduled_at=scheduled_at,
        ends_at=ends_at,
        description=description,
    )
    recipient = advising.teacher.user if is_student else advising.student.user
    notify(
        recipient,
        NotificationType.MEETING_REQUESTED,
        reference_type=MEETING_REFERENCE,
        reference_id=meeting.id,
        payload={
            "author_name": author.full_name,
            "author_type": author.type,
            "scheduled_at": _local_iso(meeting.scheduled_at),
            "description": description,
        },
    )
    return meeting


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


def _ensure_not_over(meeting: Meeting) -> None:
    """A meeting that is over takes no answer, edit, cancellation or marking (RN09, RN10, RN43).

    A meeting the teacher marked as held is refused first, with its own message, and then one whose
    time has passed."""
    if meeting.held_at is not None:
        raise ConflictError(ALREADY_HELD_MESSAGE)
    if meeting.scheduled_at <= timezone.now():
        raise ConflictError(TIME_PASSED_MESSAGE)


def _ensure_can_be_answered(meeting: Meeting) -> None:
    if meeting.status == MeetingStatus.CANCELLED:
        raise ConflictError(CANCELLED_MESSAGE)
    if meeting.status != MeetingStatus.PENDING:
        raise ConflictError(ALREADY_ANSWERED_MESSAGE)
    _ensure_not_over(meeting)


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
    # The author asked for the meeting and is the one waiting for the answer (RF38, RF41).
    notify(
        meeting.author,
        NotificationType.MEETING_ANSWERED,
        reference_type=MEETING_REFERENCE,
        reference_id=meeting.id,
        payload=_answer_payload(user, meeting, action, rejection_reason),
    )
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
    end_at: datetime.time | None = None,
    description: str | None = None,
) -> MeetingEdit:
    """The author changes the date, the times or the description of a meeting (RF21, RN09, RN39).

    Only the author edits, and only a meeting that has not happened and was not rejected or
    cancelled. A new start must be in the future, and only when it changed, and the end must stay
    after the start on the same day (RN45). Changing the date, the start or the end of an accepted
    meeting sends it back to pending (RN39). An edit that changes nothing writes nothing and
    reports no change.
    """
    meeting = _locked_meeting_of_party(user, meeting_id)
    if meeting.author_id != user.id:
        raise HttpError(404, MEETING_NOT_FOUND_MESSAGE)
    if meeting.status in (MeetingStatus.REJECTED, MeetingStatus.CANCELLED):
        raise ConflictError(NOT_EDITABLE_MESSAGE)
    _ensure_not_over(meeting)
    changes: dict[str, FieldChange] = {}
    if day is not None or at is not None or end_at is not None:
        zone = ZoneInfo(settings.TIME_ZONE)
        current = meeting.scheduled_at.astimezone(zone)
        current_end = meeting.ends_at.astimezone(zone)
        new_day = day or current.date()
        new_at = at or current.time()
        new_end_at = end_at or current_end.time()
        if new_end_at <= new_at:
            raise FieldValidationError({"end_time": END_NOT_AFTER_START_MESSAGE})
        moved, moved_end = _at(new_day, new_at), _at(new_day, new_end_at)
        if moved != meeting.scheduled_at:
            if moved <= timezone.now():
                raise FieldValidationError({"date": PAST_SCHEDULE_MESSAGE})
            changes["scheduled_at"] = FieldChange(old=meeting.scheduled_at, new=moved)
        if moved_end != meeting.ends_at:
            changes["ends_at"] = FieldChange(old=meeting.ends_at, new=moved_end)
    if description is not None and description != meeting.description:
        changes["description"] = FieldChange(old=meeting.description, new=description)
    if changes:
        for field, change in changes.items():
            setattr(meeting, field, change.new)
        if ("scheduled_at" in changes or "ends_at" in changes) and (
            meeting.status == MeetingStatus.ACCEPTED
        ):
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
    _ensure_not_over(meeting)
    meeting.status = MeetingStatus.CANCELLED
    meeting.cancellation_reason = reason
    meeting.save(update_fields=["status", "cancellation_reason"])
    return meeting


@transaction.atomic
def mark_meeting_held(user: User, meeting_id: int) -> Meeting:
    """The advising teacher marks an accepted meeting that has not happened as held (RF57, RN43).

    The mark records the moment and the author and cannot be undone. It is refused for a pending,
    rejected or cancelled meeting, for one already marked and for one whose time has passed (which
    is already held). Another teacher, a Student and an ended advising get the same 404 as a
    meeting the user cannot reach (ADR 0004). It sends no notification.
    """
    meeting = _locked_meeting_of_party(user, meeting_id)
    if meeting.advising.teacher.user_id != user.id:
        raise HttpError(404, MEETING_NOT_FOUND_MESSAGE)
    if meeting.status == MeetingStatus.CANCELLED:
        raise ConflictError(CANCELLED_MESSAGE)
    if meeting.status != MeetingStatus.ACCEPTED:
        raise ConflictError(NOT_ACCEPTED_MESSAGE)
    _ensure_not_over(meeting)
    meeting.held_at = timezone.now()
    meeting.held_by = user
    meeting.save(update_fields=["held_at", "held_by"])
    return meeting


@transaction.atomic
def write_meeting_notes(user: User, meeting_id: int, notes: str) -> Meeting:
    """The advising teacher adds, edits or removes the notes of a held meeting (RF58, RN44).

    A meeting is held when the teacher marked it (RN43) or when it is accepted and its time has
    passed. An empty text, once trimmed, removes the notes. Another teacher and an ended advising,
    whose notes are frozen, get the same 404 as a meeting the user cannot reach (ADR 0004); a
    meeting that is not held is refused with 409. It sends no notification.
    """
    meeting = _locked_meeting_of_party(user, meeting_id)
    if meeting.advising.teacher.user_id != user.id:
        raise HttpError(404, MEETING_NOT_FOUND_MESSAGE)
    if not is_held(meeting, timezone.now()):
        raise ConflictError(NOT_HELD_MESSAGE)
    meeting.notes = notes
    meeting.save(update_fields=["notes"])
    return meeting
