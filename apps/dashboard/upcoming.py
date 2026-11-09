from datetime import date

from django.db.models import QuerySet

from apps.activities.lateness import with_overdue
from apps.activities.models import Activity, ActivityStatus

UPCOMING_LIMIT = 5
OPEN_STATUSES = (ActivityStatus.PENDING, ActivityStatus.IN_PROGRESS, ActivityStatus.IN_REVIEW)


def upcoming_activities(activities: QuerySet[Activity], today: date) -> dict:
    """The open activities by deadline, so the late ones come first, at most 5 (RF50, RF51).

    One extra row is read to say whether there are more.
    """
    rows = list(
        with_overdue(activities, today)
        .filter(status__in=OPEN_STATUSES)
        .order_by("deadline", "id")[: UPCOMING_LIMIT + 1]
    )
    return {"items": rows[:UPCOMING_LIMIT], "has_more": len(rows) > UPCOMING_LIMIT}
