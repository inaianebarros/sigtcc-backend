from django.db import transaction
from ninja.errors import HttpError

from apps.accounts.models import Student, Teacher
from config.errors import ConflictError

from .models import Advising, AdvisingStatus, ThesisTopic

NO_ACTIVE_ADVISING_MESSAGE = "O tema do TCC só pode ser registrado com uma orientação ativa."
STUDENT_NOT_FOUND_MESSAGE = "Aluno não encontrado."


def active_advising_of_student(student: Student) -> Advising | None:
    return (
        Advising.objects.filter(student=student, status=AdvisingStatus.ACTIVE)
        .select_related("thesis_topic")
        .first()
    )


def active_advising_between(teacher: Teacher, student_id: int) -> Advising:
    advising = (
        Advising.objects.filter(
            student__user_id=student_id, teacher=teacher, status=AdvisingStatus.ACTIVE
        )
        .select_related("thesis_topic")
        .first()
    )
    if advising is None:
        raise HttpError(404, STUDENT_NOT_FOUND_MESSAGE)
    return advising


def topic_of(advising: Advising | None) -> ThesisTopic | None:
    if advising is None:
        return None
    return getattr(advising, "thesis_topic", None)


@transaction.atomic
def write_topic(advising: Advising | None, description: str) -> tuple[ThesisTopic, bool]:
    """Creates or replaces the topic of an active advising; returns it and whether it is new."""
    if advising is None:
        raise ConflictError(NO_ACTIVE_ADVISING_MESSAGE)
    locked = Advising.objects.select_for_update().get(pk=advising.pk)
    if locked.status != AdvisingStatus.ACTIVE:
        raise ConflictError(NO_ACTIVE_ADVISING_MESSAGE)
    topic, created = ThesisTopic.objects.update_or_create(
        advising=locked, defaults={"description": description}
    )
    return topic, created
