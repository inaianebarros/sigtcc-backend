import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Student
from apps.advising.models import Advising, AdvisingStatus
from config.errors import ConflictError, FieldValidationError

from .models import Meeting

PAST_SCHEDULE_MESSAGE = "A data e a hora devem ser posteriores ao momento atual."
NO_ACTIVE_ADVISING_MESSAGE = "Você precisa ter um orientador ativo para solicitar uma reunião."


def to_scheduled_at(day: datetime.date, at: datetime.time) -> datetime.datetime:
    """The date and the time as given in the project time zone, strictly after now (RF17)."""
    moment = datetime.datetime.combine(day, at, tzinfo=ZoneInfo(settings.TIME_ZONE))
    if moment <= timezone.now():
        raise FieldValidationError({"date": PAST_SCHEDULE_MESSAGE})
    return moment


@transaction.atomic
def request_meeting_as_student(
    student: Student,
    day: datetime.date,
    at: datetime.time,
    description: str,
) -> Meeting:
    """A Student asks their active Advisor for a Meeting (RF17, RN08).

    The advising row is locked by the same query that checks it is active, so an advising that
    ends meanwhile is answered with the same 409 and nothing is created (ADR 0004).
    """
    scheduled_at = to_scheduled_at(day, at)
    advising = (
        Advising.objects.select_for_update()
        .filter(student=student, status=AdvisingStatus.ACTIVE)
        .first()
    )
    if advising is None:
        raise ConflictError(NO_ACTIVE_ADVISING_MESSAGE)
    # O autor é quem criou: a resposta, a edição e o cancelamento sabem quem é quem (RN09, RN10).
    return Meeting.objects.create(
        advising=advising,
        author=student.user,
        scheduled_at=scheduled_at,
        description=description,
    )
