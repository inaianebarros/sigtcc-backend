from django.db.models import F, Q
from django.utils import timezone
from ninja.errors import HttpError

from apps.accounts.models import User
from apps.activities.models import Activity, ActivityStatus
from apps.meetings.models import Meeting, MeetingStatus
from apps.meetings.situations import is_over

from .models import Advising, AdvisingStatus
from .services import own_advisings

ADVISING_NOT_FOUND_MESSAGE = "Orientação não encontrada."

#: The activities that stay in the report: the ones that were concluded and the ones archived when
#: the advising ended (RN07). The others are not a record of what happened.
REPORT_ACTIVITY_STATUSES = (ActivityStatus.COMPLETED, ActivityStatus.ARCHIVED)


def report_meetings(advising: Advising):
    """The meetings that took place in the advising (RN43): accepted and over, by the clock or by
    the teacher's mark. Pending, rejected, cancelled and never-held ones are not records."""
    return Meeting.objects.filter(
        Q(status=MeetingStatus.ACCEPTED) & is_over(timezone.now()), advising=advising
    )


def report_activities(advising: Advising):
    """The activities of the advising that were not deleted and are concluded or archived."""
    return Activity.objects.filter(
        advising=advising, deleted_at__isnull=True, status__in=REPORT_ACTIVITY_STATUSES
    )


def _ended_of(user: User, advising_id: int):
    """The ended advisings of the user with that id (none or one), and the path to the other party."""
    advisings, counterpart = own_advisings(user)
    return advisings.filter(pk=advising_id, status=AdvisingStatus.ENDED), counterpart


def read_ended_advising(user: User, advising_id: int) -> Advising:
    """The ended advising of the user, or the 404 that anyone else, an unknown id and an advising
    that is still active get alike (RN47), so nothing reveals what exists."""
    advising, _ = _ended_of(user, advising_id)
    advising = advising.first()
    if advising is None:
        raise HttpError(404, ADVISING_NOT_FOUND_MESSAGE)
    return advising


def advising_report(user: User, advising_id: int) -> Advising:
    """The report of an ended advising of the user (RF61): the header and the general totals.

    Only the student or the teacher of an ended advising read it (RN47)."""
    advisings, counterpart = _ended_of(user, advising_id)
    advising = (
        advisings.select_related("thesis_topic")
        .annotate(
            counterpart_id=F(f"{counterpart}__id"),
            counterpart_name=F(f"{counterpart}__full_name"),
        )
        .first()
    )
    if advising is None:
        raise HttpError(404, ADVISING_NOT_FOUND_MESSAGE)
    advising.meetings_count = report_meetings(advising).count()
    advising.activities_count = report_activities(advising).count()
    advising.records_count = advising.meetings_count + advising.activities_count
    return advising
