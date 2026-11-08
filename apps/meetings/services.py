import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from ninja.errors import HttpError

from apps.accounts.models import Student, Teacher, User, UserType
from apps.advising.linked_student_profile import STUDENT_NOT_FOUND_MESSAGE
from apps.advising.models import Advising, AdvisingStatus
from config.errors import ConflictError, FieldValidationError

from .models import Meeting

PAST_SCHEDULE_MESSAGE = "A data e a hora devem ser posteriores ao momento atual."
NO_ACTIVE_ADVISING_MESSAGE = "Você precisa ter um orientador ativo para solicitar uma reunião."
STUDENT_ID_NOT_ALLOWED_MESSAGE = "O aluno não informa o aluno da reunião."
STUDENT_ID_REQUIRED_MESSAGE = "Informe o aluno."


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
