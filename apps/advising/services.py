from dataclasses import dataclass
from datetime import datetime

from django.db import IntegrityError, transaction
from django.utils import timezone
from ninja.errors import HttpError

from apps.accounts.models import Student, Teacher
from apps.notifications.models import (
    ADVISING_REQUEST_REFERENCE,
    Notification,
    NotificationType,
)
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

ACTIVE_ADVISING_MESSAGE = "Você já tem um orientador ativo."
PENDING_REQUEST_MESSAGE = "Você já tem uma solicitação pendente."
OTHER_COURSE_MESSAGE = "Este professor não orienta o seu curso."
UNAVAILABLE_TEACHER_MESSAGE = "Este professor não está disponível para novas solicitações."
ALREADY_ANSWERED_MESSAGE = "Esta solicitação já foi respondida."
DEADLINE_PASSED_MESSAGE = "O prazo para responder esta solicitação terminou."
INACTIVE_STUDENT_MESSAGE = "Este aluno não está mais ativo."
STUDENT_HAS_ADVISING_MESSAGE = "O aluno já tem um orientador ativo."


def eligibility_block_of(student: Student, cutoff: datetime | None = None) -> BlockedReason | None:
    """Why the student cannot send a new request (RN04, RN05), or None. Active advising wins."""
    if Advising.objects.filter(student=student, status=AdvisingStatus.ACTIVE).exists():
        return BlockedReason.ACTIVE_ADVISING
    if AdvisingRequest.objects.filter(student=student).valid_pending(cutoff).exists():
        return BlockedReason.PENDING_REQUEST
    return None


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
    Notification.objects.create(
        recipient=teacher.user,
        type=NotificationType.ADVISING_REQUEST_RECEIVED,
        reference_type=ADVISING_REQUEST_REFERENCE,
        reference_id=advising_request.id,
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
    if Advising.objects.filter(student=student, status=AdvisingStatus.ACTIVE).exists():
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
    Notification.objects.create(
        recipient=student.user,
        type=NotificationType.ADVISING_REQUEST_ANSWERED,
        reference_type=ADVISING_REQUEST_REFERENCE,
        reference_id=advising_request.id,
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
    return StudentRequestStatus(
        latest_request, active_advising, eligibility_block_of(student, cutoff)
    )
