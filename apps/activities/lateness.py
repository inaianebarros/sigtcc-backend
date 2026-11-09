from datetime import date

from django.db.models import BooleanField, Case, Q, QuerySet, Value, When
from django.utils import timezone

from .models import ActivityStatus

NEVER_LATE = (ActivityStatus.COMPLETED, ActivityStatus.ARCHIVED)


def overdue_condition(today: date | None = None, prefix: str = "") -> Q:
    """RN24: the deadline is a date, so an activity is late from the day after it.

    The date is the server date in the project time zone, passed as a parameter so that
    every caller of one request (list, filter, dashboard counts) agrees on the same day. The
    prefix reaches the activity from a related model, such as "activities__" from an advising.
    """
    if today is None:
        today = timezone.localdate()
    return Q(**{f"{prefix}deadline__lt": today}) & ~Q(**{f"{prefix}status__in": NEVER_LATE})


def with_overdue(activities: QuerySet, today: date | None = None) -> QuerySet:
    return activities.annotate(
        overdue=Case(
            When(overdue_condition(today), then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        )
    )
