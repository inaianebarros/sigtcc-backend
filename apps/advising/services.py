from dataclasses import dataclass
from datetime import datetime

from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone
from ninja.errors import HttpError

from apps.accounts.models import Student, Teacher
from apps.activities.models import Activity, ActivityStatus
from apps.meetings.models import SYSTEM_CANCELLATION_REASON, Meeting, MeetingStatus
from apps.notifications.models import (
    ADVISING_REFERENCE,
    ADVISING_REQUEST_REFERENCE,
    NotificationType,
)
from apps.notifications.services import notify
from config.errors import ConflictError

from .deadlines import answer_cutoff
from .models import (
    ANSWERED_STATUSES,
    Advising,
    AdvisingRequest,
    AdvisingRequestStatus,
    AdvisingStatus,
    AnswerAction,
    BlockedReason,
)
from .name_search import filter_by_name

ARCHIVED_ON_END = (ActivityStatus.PENDING, ActivityStatus.IN_PROGRESS, ActivityStatus.IN_REVIEW)
CANCELLED_ON_END = (MeetingStatus.PENDING, MeetingStatus.ACCEPTED)
ALREADY_ENDED_MESSAGE = "Esta orientação já foi encerrada."
ACTIVE_ADVISING_MESSAGE = "Você já tem um orientador ativo."
PENDING_REQUEST_MESSAGE = "Você já tem uma solicitação pendente."
OTHER_COURSE_MESSAGE = "Este professor não orienta o seu curso."
UNAVAILABLE_TEACHER_MESSAGE = "Este professor não está disponível para novas solicitações."
ALREADY_ANSWERED_MESSAGE = "Esta solicitação já foi respondida."
DEADLINE_PASSED_MESSAGE = "O prazo para responder esta solicitação terminou."
INACTIVE_STUDENT_MESSAGE = "Este aluno não está mais ativo."
STUDENT_HAS_ADVISING_MESSAGE = "O aluno já tem um orientador ativo."


def has_active_advising(student: Student) -> bool:
    return Advising.objects.filter(student=student, status=AdvisingStatus.ACTIVE).exists()


def _block_reason(
    student: Student, has_advising: bool, cutoff: datetime | None
) -> BlockedReason | None:
    if has_advising:
        return BlockedReason.ACTIVE_ADVISING
    if AdvisingRequest.objects.filter(student=student).valid_pending(cutoff).exists():
        return BlockedReason.PENDING_REQUEST
    return None


def eligibility_block_of(student: Student, cutoff: datetime | None = None) -> BlockedReason | None:
    """Why the student cannot send a new request (RN04, RN05), or None. Active advising wins."""
    return _block_reason(student, has_active_advising(student), cutoff)


_BLOCK_MESSAGES = {
    BlockedReason.ACTIVE_ADVISING: ACTIVE_ADVISING_MESSAGE,
    BlockedReason.PENDING_REQUEST: PENDING_REQUEST_MESSAGE,
}


def _ensure_teacher_accepts_requests(student: Student, teacher: Teacher) -> None:
    if not teacher.courses.filter(pk=student.course_id).exists():
        raise ConflictError(OTHER_COURSE_MESSAGE)
    if not teacher.is_available:
        raise ConflictError(UNAVAILABLE_TEACHER_MESSAGE)


@transaction.atomic
def request_advising(student: Student, teacher_id: int) -> AdvisingRequest:
    locked_student = Student.objects.select_for_update().get(pk=student.pk)
    teacher = (
        Teacher.objects.select_related("user")
        .filter(user_id=teacher_id, user__is_active=True)
        .first()
    )
    if teacher is None:
        raise HttpError(404, "Professor não encontrado.")

    blocked_reason = eligibility_block_of(locked_student)
    if blocked_reason is not None:
        raise ConflictError(_BLOCK_MESSAGES[blocked_reason])
    _ensure_teacher_accepts_requests(locked_student, teacher)
    AdvisingRequest.objects.filter(student=locked_student).overdue_pending().update(
        status=AdvisingRequestStatus.UNANSWERED
    )

    try:
        with transaction.atomic():
            advising_request = AdvisingRequest.objects.create(
                student=locked_student, teacher=teacher
            )
    except IntegrityError as error:
        raise ConflictError(PENDING_REQUEST_MESSAGE) from error
    notify(
        teacher.user,
        NotificationType.ADVISING_REQUEST_RECEIVED,
        reference_type=ADVISING_REQUEST_REFERENCE,
        reference_id=advising_request.id,
        payload={"student_name": student.user.full_name},
    )
    return advising_request


def list_received_requests(teacher: Teacher, status: AdvisingRequestStatus | None = None):
    received = (
        AdvisingRequest.objects.filter(teacher=teacher)
        .with_effective_status()
        .select_related("student__user", "student__course")
        .prefetch_related("student__areas_of_interest")
        .order_by("-requested_at", "-id")
    )
    return received.filter(effective_status=status) if status else received


def _ensure_can_be_answered(advising_request: AdvisingRequest, student: Student) -> None:
    if advising_request.effective_status in ANSWERED_STATUSES:
        raise ConflictError(ALREADY_ANSWERED_MESSAGE)
    if advising_request.effective_status == AdvisingRequestStatus.UNANSWERED:
        raise ConflictError(DEADLINE_PASSED_MESSAGE)
    if not student.user.is_active:
        raise ConflictError(INACTIVE_STUDENT_MESSAGE)


def _start_advising(advising_request: AdvisingRequest, student: Student, teacher: Teacher) -> None:
    if has_active_advising(student):
        raise ConflictError(STUDENT_HAS_ADVISING_MESSAGE)
    try:
        with transaction.atomic():
            Advising.objects.create(student=student, teacher=teacher, request=advising_request)
    except IntegrityError as error:
        raise ConflictError(STUDENT_HAS_ADVISING_MESSAGE) from error


@transaction.atomic
def answer_request(teacher: Teacher, request_id: int, action: AnswerAction) -> AdvisingRequest:
    # Trava o Aluno antes da solicitação, na mesma ordem de request_advising, para evitar deadlock.
    student_id = (
        AdvisingRequest.objects.filter(pk=request_id, teacher=teacher)
        .values_list("student_id", flat=True)
        .first()
    )
    if student_id is None:
        raise HttpError(404, "Solicitação não encontrada.")
    student = Student.objects.select_for_update().select_related("user").get(pk=student_id)
    advising_request = (
        AdvisingRequest.objects.select_for_update().with_effective_status().get(pk=request_id)
    )
    _ensure_can_be_answered(advising_request, student)

    if action == AnswerAction.ACCEPT:
        _start_advising(advising_request, student, teacher)
        advising_request.status = AdvisingRequestStatus.ACCEPTED
    else:
        advising_request.status = AdvisingRequestStatus.REJECTED
    advising_request.responded_at = timezone.now()
    advising_request.save(update_fields=["status", "responded_at"])
    notify(
        student.user,
        NotificationType.ADVISING_REQUEST_ANSWERED,
        reference_type=ADVISING_REQUEST_REFERENCE,
        reference_id=advising_request.id,
        payload={
            "teacher_name": teacher.user.full_name,
            "decision": "accepted" if action == AnswerAction.ACCEPT else "rejected",
        },
    )
    return AdvisingRequest.objects.with_effective_status().get(pk=request_id)


@dataclass(frozen=True)
class StudentRequestStatus:
    request: AdvisingRequest | None
    advising: Advising | None
    blocked_reason: BlockedReason | None

    @property
    def can_request(self) -> bool:
        return self.blocked_reason is None


def student_request_status(student: Student) -> StudentRequestStatus:
    """Read-only view of the student's latest request, active advising and eligibility."""
    cutoff = answer_cutoff()
    latest_request = (
        AdvisingRequest.objects.filter(student=student)
        .with_effective_status(cutoff)
        .select_related("teacher__user")
        .order_by("-requested_at", "-id")
        .first()
    )
    active_advising = (
        Advising.objects.filter(student=student, status=AdvisingStatus.ACTIVE)
        .select_related("teacher__user")
        .first()
    )
    blocked_reason = _block_reason(student, active_advising is not None, cutoff)
    return StudentRequestStatus(latest_request, active_advising, blocked_reason)


@dataclass(frozen=True)
class EndingSummary:
    """What ending an advising changed: the open activities archived and the meetings cancelled."""

    archived_activities: int
    cancelled_meetings: int


def close_advising(advising: Advising, reason: str) -> EndingSummary:
    """Ends a locked advising and everything that depends on it (RN07, RN34)."""
    if advising.status == AdvisingStatus.ENDED:
        raise ConflictError(ALREADY_ENDED_MESSAGE)
    now = timezone.now()
    archived = Activity.objects.filter(
        advising=advising, deleted_at__isnull=True, status__in=ARCHIVED_ON_END
    ).update(status=ActivityStatus.ARCHIVED)
    cancelled = Meeting.objects.filter(
        advising=advising, status__in=CANCELLED_ON_END, scheduled_at__gt=now
    ).update(
        status=MeetingStatus.CANCELLED,
        cancelled_by_system=True,
        cancellation_reason=SYSTEM_CANCELLATION_REASON,
    )
    advising.status = AdvisingStatus.ENDED
    advising.ended_at = now
    advising.end_reason = reason
    advising.save(update_fields=["status", "ended_at", "end_reason"])
    return EndingSummary(archived_activities=archived, cancelled_meetings=cancelled)


@transaction.atomic
def end_advising(teacher: Teacher, advising_id: int, reason: str) -> Advising:
    advising = Advising.objects.select_for_update().filter(pk=advising_id, teacher=teacher).first()
    if advising is None:
        raise HttpError(404, "Orientação não encontrada.")
    ending = close_advising(advising, reason)
    notify(
        advising.student.user,
        NotificationType.ADVISING_ENDED,
        reference_type=ADVISING_REFERENCE,
        reference_id=advising.id,
        payload={
            "teacher_name": teacher.user.full_name,
            "archived_activities": ending.archived_activities,
            "cancelled_meetings": ending.cancelled_meetings,
        },
    )
    return advising


def _activities_in(status: ActivityStatus) -> Count:
    return Count(
        "activities",
        filter=Q(activities__status=status, activities__deleted_at__isnull=True),
    )


def list_advisees(teacher: Teacher, search: str | None = None):
    advisings = filter_by_name(
        Advising.objects.filter(teacher=teacher, status=AdvisingStatus.ACTIVE),
        "student__user__full_name",
        search,
    )
    return (
        advisings.select_related("student__user", "student__course", "thesis_topic")
        .annotate(
            pending_count=_activities_in(ActivityStatus.PENDING),
            in_progress_count=_activities_in(ActivityStatus.IN_PROGRESS),
            in_review_count=_activities_in(ActivityStatus.IN_REVIEW),
            completed_count=_activities_in(ActivityStatus.COMPLETED),
        )
        .order_by("-started_at", "-id")
    )
