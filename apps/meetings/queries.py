import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Case, F, Q, QuerySet, When
from django.utils import timezone

from apps.accounts.models import User, UserType
from config.errors import FieldValidationError

from .models import Meeting
from .situations import is_over, with_situation

STUDENT_FILTER_NOT_ALLOWED_MESSAGE = "O aluno não filtra por aluno."
PERIOD_MESSAGE = "A data inicial não pode ser depois da data final."
MAX_PERIOD_DAYS = 42
PERIOD_TOO_LONG_MESSAGE = f"O período não pode passar de {MAX_PERIOD_DAYS} dias."
DATE_OUT_OF_RANGE_MESSAGE = "Informe uma data final anterior a 31/12/9999."
HIDDEN_STUDENTS_NOT_ALLOWED_MESSAGE = "O aluno não oculta alunos."


def _start_of(day: datetime.date) -> datetime.datetime:
    return datetime.datetime.combine(day, datetime.time.min, tzinfo=ZoneInfo(settings.TIME_ZONE))


def _meetings_of(user: User) -> QuerySet[Meeting]:
    return Meeting.objects.filter(Q(advising__student__user=user) | Q(advising__teacher__user=user))


_RELATED = ("author", "advising", "advising__student__user", "advising__teacher__user")


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
    meetings = _meetings_of(user)
    if student_id is not None:
        meetings = meetings.filter(advising__student__user_id=student_id)
    if date_from is not None:
        meetings = meetings.filter(scheduled_at__gte=_start_of(date_from))
    if date_to is not None:
        meetings = meetings.filter(scheduled_at__lt=_start_of(date_to + datetime.timedelta(days=1)))
    meetings = with_situation(meetings, now)
    if situation is not None:
        meetings = meetings.filter(situation=situation)
    past = is_over(now)
    return (
        meetings.select_related(*_RELATED)
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


def meetings_in_period(
    user: User,
    date_from: datetime.date,
    date_to: datetime.date,
    situations: list[str] | None,
    hidden_student_ids: list[int] | None,
    now: datetime.datetime | None = None,
) -> QuerySet[Meeting]:
    """Every meeting of the user between two dates, for the calendar (RF19, ADR 0015).

    The dates are inclusive and read in the project time zone, the window has at most 42 days (the
    six weeks of the month grid) and nothing is paginated. Advisings active or ended count (RN28,
    RN32). The Teacher hides advisees, so a new advisee shows up without any choice; the Student
    has no such filter. Ordered by start, then id.
    """
    if hidden_student_ids and user.type == UserType.STUDENT:
        raise FieldValidationError({"hidden_student_ids": HIDDEN_STUDENTS_NOT_ALLOWED_MESSAGE})
    if date_from > date_to:
        raise FieldValidationError({"date_from": PERIOD_MESSAGE})
    if date_to == datetime.date.max:
        raise FieldValidationError({"date_to": DATE_OUT_OF_RANGE_MESSAGE})
    if (date_to - date_from).days + 1 > MAX_PERIOD_DAYS:
        raise FieldValidationError({"date_to": PERIOD_TOO_LONG_MESSAGE})
    meetings = _meetings_of(user).filter(
        scheduled_at__gte=_start_of(date_from),
        scheduled_at__lt=_start_of(date_to + datetime.timedelta(days=1)),
    )
    if hidden_student_ids:
        meetings = meetings.exclude(advising__student__user_id__in=hidden_student_ids)
    meetings = with_situation(meetings, now or timezone.now())
    if situations:
        meetings = meetings.filter(situation__in=situations)
    return meetings.select_related(*_RELATED).order_by("scheduled_at", "id")
