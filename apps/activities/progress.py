from dataclasses import dataclass
from datetime import date

from django.db.models import Count, Q, QuerySet

from .lateness import overdue_condition
from .models import Activity, ActivityStatus


def counted_activities() -> QuerySet[Activity]:
    """The activities that count in any indicator: neither archived nor deleted (RN35).

    The callers narrow it to the active advisings they summarise, so an ended advising never
    counts.
    """
    return Activity.objects.filter(deleted_at__isnull=True).exclude(status=ActivityStatus.ARCHIVED)


@dataclass(frozen=True)
class ActivityCounts:
    total: int
    pending: int
    in_progress: int
    in_review: int
    completed: int
    overdue: int

    @property
    def progress_percent(self) -> int | None:
        return progress_percent(self.completed, self.total)


def progress_percent(completed: int, total: int) -> int | None:
    """RN36: completed over total, truncated, so it is never 100 while an activity is open.

    With no activities there is no percentage (and no division by zero): `None`.
    """
    if total == 0:
        return None
    return completed * 100 // total


def count_activities(activities: QuerySet[Activity], today: date) -> ActivityCounts:
    """The indicators of already-scoped activities in one query (RN35, RN37).

    `today` is passed in so the counts, the lists and the lateness flags of one request agree on
    the same day.
    """
    counts = activities.aggregate(
        total=Count("id"),
        pending=Count("id", filter=Q(status=ActivityStatus.PENDING)),
        in_progress=Count("id", filter=Q(status=ActivityStatus.IN_PROGRESS)),
        in_review=Count("id", filter=Q(status=ActivityStatus.IN_REVIEW)),
        completed=Count("id", filter=Q(status=ActivityStatus.COMPLETED)),
        overdue=Count("id", filter=overdue_condition(today)),
    )
    return ActivityCounts(**counts)
