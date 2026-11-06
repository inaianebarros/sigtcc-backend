from django.db import IntegrityError, transaction
from ninja.errors import HttpError

from apps.accounts.models import Student, Teacher
from apps.notifications.models import (
    ADVISING_REQUEST_REFERENCE,
    Notification,
    NotificationType,
)
from config.errors import ConflictError

from .models import Advising, AdvisingRequest, AdvisingRequestStatus, AdvisingStatus

ACTIVE_ADVISING_MESSAGE = "Você já tem um orientador ativo."
PENDING_REQUEST_MESSAGE = "Você já tem uma solicitação pendente."
OTHER_COURSE_MESSAGE = "Este professor não orienta o seu curso."
UNAVAILABLE_TEACHER_MESSAGE = "Este professor não está disponível para novas solicitações."


def _ensure_no_active_advising(student: Student) -> None:
    if Advising.objects.filter(student=student, status=AdvisingStatus.ACTIVE).exists():
        raise ConflictError(ACTIVE_ADVISING_MESSAGE)


def _ensure_no_pending_request(student: Student) -> None:
    """Falha se há solicitação pendente no prazo; marca como não respondida a que venceu."""
    student_requests = AdvisingRequest.objects.filter(student=student)
    if student_requests.valid_pending().exists():
        raise ConflictError(PENDING_REQUEST_MESSAGE)
    student_requests.overdue_pending().update(status=AdvisingRequestStatus.UNANSWERED)


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

    _ensure_no_active_advising(locked_student)
    _ensure_no_pending_request(locked_student)
    _ensure_teacher_accepts_requests(locked_student, teacher)

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
