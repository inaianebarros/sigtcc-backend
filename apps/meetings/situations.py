from datetime import datetime
from enum import StrEnum

from django.db.models import Case, CharField, Q, QuerySet, Value, When
from django.utils import timezone

from .models import MeetingStatus


class MeetingSituation(StrEnum):
    """What a meeting is now, derived from its status and the clock; never persisted."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    HELD = "held"
    EXPIRED = "expired"


def is_past(now: datetime) -> Q:
    """A meeting is past from its time on, as the answer, the edit and the cancel decide."""
    return Q(scheduled_at__lte=now)


def accepted_and_future(now: datetime | None = None) -> Q:
    """The accepted meetings that have not happened yet (the dashboards, RF50 and RF51)."""
    return Q(status=MeetingStatus.ACCEPTED) & ~is_past(now or timezone.now())


def with_situation(meetings: QuerySet, now: datetime) -> QuerySet:
    """Annotates `situation`, computed in the database against one instant of the request."""
    past = is_past(now)
    return meetings.annotate(
        situation=Case(
            When(status=MeetingStatus.REJECTED, then=Value(MeetingSituation.REJECTED)),
            When(status=MeetingStatus.CANCELLED, then=Value(MeetingSituation.CANCELLED)),
            When(accepted_and_future(now), then=Value(MeetingSituation.ACCEPTED)),
            When(status=MeetingStatus.ACCEPTED, then=Value(MeetingSituation.HELD)),
            When(Q(status=MeetingStatus.PENDING) & past, then=Value(MeetingSituation.EXPIRED)),
            default=Value(MeetingSituation.PENDING),
            output_field=CharField(),
        )
    )


def can_answer(situation: str, is_author: bool, advising_active: bool) -> bool:
    """The other party answers a pending meeting that has not happened (RN11, #37)."""
    return advising_active and situation == MeetingSituation.PENDING and not is_author


def can_edit(situation: str, is_author: bool, advising_active: bool) -> bool:
    """The author edits a pending or accepted meeting that has not happened (RN09, #38)."""
    return (
        advising_active
        and is_author
        and situation in (MeetingSituation.PENDING, MeetingSituation.ACCEPTED)
    )


def can_cancel(situation: str, is_author: bool, advising_active: bool) -> bool:
    """The author cancels a pending one and either party an accepted one (RN10, #38)."""
    if not advising_active:
        return False
    if situation == MeetingSituation.PENDING:
        return is_author
    return situation == MeetingSituation.ACCEPTED
