import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Case, F, Q, QuerySet, When
from django.utils import timezone

from apps.accounts.models import User, UserType
from config.errors import FieldValidationError

from .models import Meeting
from .situations import is_past, with_situation

STUDENT_FILTER_NOT_ALLOWED_MESSAGE = "O aluno não filtra por aluno."
PERIOD_MESSAGE = "A data inicial não pode ser depois da data final."


def _start_of(day: datetime.date) -> datetime.datetime:
    return datetime.datetime.combine(day, datetime.time.min, tzinfo=ZoneInfo(settings.TIME_ZONE))


def list_meetings(
    user: User,
    situation: str | None,
    date_from: datetime.date | None,
    date_to: datetime.date | None,
    student_id: int | None,
    now: datetime.datetime | None = None,
) -> QuerySet[Meeting]:
    """The meetings of every advising of the user, active or ended (RF19), filtered and ordered."""
    if student_id is not None and user.type == UserType.STUDENT:
        raise FieldValidationError({"student_id": STUDENT_FILTER_NOT_ALLOWED_MESSAGE})
    if date_from is not None and date_to is not None and date_from > date_to:
        raise FieldValidationError({"date_from": PERIOD_MESSAGE})
    now = now or timezone.now()
    meetings = Meeting.objects.filter(
        Q(advising__student__user=user) | Q(advising__teacher__user=user)
    )
    if student_id is not None:
        meetings = meetings.filter(advising__student__user_id=student_id)
    if date_from is not None:
        meetings = meetings.filter(scheduled_at__gte=_start_of(date_from))
    if date_to is not None:
        meetings = meetings.filter(scheduled_at__lt=_start_of(date_to + datetime.timedelta(days=1)))
    meetings = with_situation(meetings, now)
    if situation is not None:
        meetings = meetings.filter(situation=situation)
    past = is_past(now)
    return (
        meetings.select_related(
            "author", "advising", "advising__student__user", "advising__teacher__user"
        )
        .annotate(
            upcoming_at=Case(When(~past, then=F("scheduled_at"))),
            elapsed_at=Case(When(past, then=F("scheduled_at"))),
        )
        .order_by(
            F("upcoming_at").asc(nulls_last=True),
            F("elapsed_at").desc(nulls_last=True),
            "id",
        )
    )
