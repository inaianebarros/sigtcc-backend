from collections.abc import Callable
from datetime import datetime

from django.db.models import QuerySet
from django.utils import timezone

from apps.meetings.models import Meeting
from apps.meetings.situations import accepted_and_future

from .upcoming import UPCOMING_LIMIT

SUMMARY_LENGTH = 100


def summarize(text: str, limit: int = SUMMARY_LENGTH) -> str:
    """The description cut to `limit` characters, with an ellipsis when it was longer."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def upcoming_meetings(meetings: QuerySet[Meeting], now: datetime) -> list[Meeting]:
    """The accepted meetings that have not happened yet, the nearest first (RF50, RF51).

    The condition is the one of the meetings module (#36). One extra row is read so the caller can
    say whether there are more than the limit.
    """
    return list(
        meetings.filter(accepted_and_future(now)).order_by("scheduled_at", "id")[
            : UPCOMING_LIMIT + 1
        ]
    )


def meeting_time(meeting: Meeting) -> datetime:
    """The meeting time in the project time zone, as the meetings API gives it."""
    return timezone.localtime(meeting.scheduled_at)


def meetings_block(rows: list[Meeting], other_party: Callable[[Meeting], dict]) -> dict:
    """The dashboard block: at most 5 meetings, each with the other party, and whether more exist."""
    items = [
        {
            "id": meeting.id,
            "scheduled_at": meeting_time(meeting),
            "ends_at": timezone.localtime(meeting.ends_at),
            **other_party(meeting),
            "description": summarize(meeting.description),
        }
        for meeting in rows[:UPCOMING_LIMIT]
    ]
    return {"items": items, "has_more": len(rows) > UPCOMING_LIMIT}
