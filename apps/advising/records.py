from typing import Literal

from django.db.models import CharField, F, Value
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.activities.models import Activity
from apps.meetings.models import Meeting

from .models import Advising
from .report import report_activities, report_meetings

RecordType = Literal["meeting", "activity"]


class RecordFeed:
    """The records of the report as one paginated sequence (RF61).

    Meetings and activities live in different tables, so the feed is a UNION of their keys
    (type, id, day) ordered by day, the most recent first, then by type and the higher id first.
    Counting and slicing happen in the database on the keys alone; only the records of the
    requested page are loaded, one query per type, so the queries do not grow with the records.
    It is list-like (`len` and slicing) for the paginator.
    """

    def __init__(self, advising: Advising, record_type: RecordType | None):
        keys = []
        if record_type in (None, "meeting"):
            keys.append(
                report_meetings(advising)
                .annotate(kind=Value("meeting", CharField()), day=TruncDate("scheduled_at"))
                .values("kind", "id", "day")
            )
        if record_type in (None, "activity"):
            keys.append(
                report_activities(advising)
                .annotate(kind=Value("activity", CharField()), day=F("deadline"))
                .values("kind", "id", "day")
            )
        feed = keys[0].union(*keys[1:], all=True) if len(keys) > 1 else keys[0]
        self._keys = feed.order_by("-day", "kind", "-id")

    def __len__(self) -> int:
        return self._keys.count()

    def __getitem__(self, window: slice) -> list[dict]:
        page = list(self._keys[window])
        meetings = Meeting.objects.in_bulk([key["id"] for key in page if key["kind"] == "meeting"])
        activities = Activity.objects.in_bulk(
            [key["id"] for key in page if key["kind"] == "activity"]
        )
        return [
            _meeting_record(meetings[key["id"]], key["day"])
            if key["kind"] == "meeting"
            else _activity_record(activities[key["id"]])
            for key in page
        ]


def _meeting_record(meeting: Meeting, day) -> dict:
    return {
        "type": "meeting",
        "id": meeting.id,
        "date": day,
        "starts_at": timezone.localtime(meeting.scheduled_at),
        "ends_at": timezone.localtime(meeting.ends_at),
        "description": meeting.description,
        "notes": meeting.notes,
    }


def _activity_record(activity: Activity) -> dict:
    return {
        "type": "activity",
        "id": activity.id,
        "date": activity.deadline,
        "deadline": activity.deadline,
        "title": activity.title,
        "description": activity.description,
        "status": activity.status,
    }
