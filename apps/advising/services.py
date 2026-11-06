from django.db import IntegrityError, transaction
from ninja.errors import HttpError

from apps.accounts.models import Student, Teacher
from apps.notifications.models import Notification, NotificationType
from config.errors import ConflictError

from .deadlines import answer_cutoff
from .models import Advising, AdvisingRequest, AdvisingRequestStatus, AdvisingStatus

ACTIVE_ADVISING_MESSAGE = "Você já tem um orientador ativo."
PENDING_REQUEST_MESSAGE = "Você já tem uma solicitação pendente."
OTHER_COURSE_MESSAGE = "Este professor não orienta o seu curso."
UNAVAILABLE_TEACHER_MESSAGE = "Este professor não está disponível para novas solicitações."


def _release_overdue_pending_request(student: Student) -> None:
    pending = AdvisingRequest.objects.filter(
        student=student, status=AdvisingRequestStatus.PENDING
    ).first()
    if pending is None:
        return
    if pending.requested_at > answer_cutoff():
        raise ConflictError(PENDING_REQUEST_MESSAGE)
    pending.status = AdvisingRequestStatus.UNANSWERED
    pending.save(update_fields=["status"])


def _ensure_can_request(student: Student, teacher: Teacher) -> None:
    if Advising.objects.filter(student=student, status=AdvisingStatus.ACTIVE).exists():
        raise ConflictError(ACTIVE_ADVISING_MESSAGE)
    _release_overdue_pending_request(student)
    if not teacher.courses.filter(pk=student.course_id).exists():
        raise ConflictError(OTHER_COURSE_MESSAGE)
    if not teacher.is_available:
        raise ConflictError(UNAVAILABLE_TEACHER_MESSAGE)


@transaction.atomic
def request_advising(student: Student, teacher_id: int) -> AdvisingRequest:
    student = Student.objects.select_for_update().get(pk=student.pk)
    teacher = (
        Teacher.objects.select_related("user")
        .filter(user_id=teacher_id, user__is_active=True)
        .first()
    )
    if teacher is None:
        raise HttpError(404, "Professor não encontrado.")

    _ensure_can_request(student, teacher)
    try:
        with transaction.atomic():
            advising_request = AdvisingRequest.objects.create(student=student, teacher=teacher)
    except IntegrityError as error:
        raise ConflictError(PENDING_REQUEST_MESSAGE) from error
    Notification.objects.create(
        recipient=teacher.user,
        type=NotificationType.ADVISING_REQUEST_RECEIVED,
        reference_type="advising_request",
        reference_id=advising_request.id,
    )
    return advising_request
