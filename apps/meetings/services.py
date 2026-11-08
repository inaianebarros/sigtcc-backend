import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Student, User
from apps.advising.models import Advising, AdvisingStatus
from config.errors import ConflictError, FieldValidationError

from .models import Meeting

PAST_SCHEDULE_MESSAGE = "A data e a hora devem ser posteriores ao momento atual."
STUDENT_ID_NOT_ALLOWED_MESSAGE = "O aluno não informa o aluno da reunião."
NO_ACTIVE_ADVISING_MESSAGE = "Você precisa ter um orientador ativo para solicitar uma reunião."


def schedule_of(day: datetime.date, at: datetime.time) -> datetime.datetime:
    """The date and the time as given in the project time zone, strictly after now (RF17)."""
    moment = datetime.datetime.combine(day, at, tzinfo=ZoneInfo(settings.TIME_ZONE))
    if moment <= timezone.now():
        raise FieldValidationError({"date": PAST_SCHEDULE_MESSAGE})
    return moment


def _create(
    author: User, advising: Advising, scheduled_at: datetime.datetime, description: str
) -> Meeting:
    """The single entry point that stores a Meeting, for a Student (#34) or a Teacher (#35).

    The caller has locked the active advising in the same transaction (ADR 0004). The author is
    whoever created it, so the answer, the edit and the cancellation (#37, #38) know who is who.
    """
    return Meeting.objects.create(
        advising=advising, author=author, scheduled_at=scheduled_at, description=description
    )


@transaction.atomic
def request_meeting_as_student(
    student: Student,
    day: datetime.date,
    at: datetime.time,
    description: str,
    student_id: int | None = None,
) -> Meeting:
    """A Student asks their active Advisor for a Meeting (RF17, RN08).

    The advising row is locked by the same query that checks it is active, so an advising that
    ends meanwhile is answered with the same 409 and nothing is created (ADR 0004).
    """
    if student_id is not None:
        raise FieldValidationError({"student_id": STUDENT_ID_NOT_ALLOWED_MESSAGE})
    scheduled_at = schedule_of(day, at)
    advising = (
        Advising.objects.select_for_update()
        .filter(student=student, status=AdvisingStatus.ACTIVE)
        .select_related("student__user")
        .first()
    )
    if advising is None:
        raise ConflictError(NO_ACTIVE_ADVISING_MESSAGE)
    return _create(student.user, advising, scheduled_at, description)
