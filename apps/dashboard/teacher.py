from datetime import datetime

from django.db.models import Count, Q
from django.db.models.functions import Lower
from django.utils import timezone

from apps.accounts.models import Teacher
from apps.activities.lateness import overdue_condition
from apps.activities.models import ActivityStatus
from apps.activities.progress import count_activities, counted_activities
from apps.advising.models import Advising, AdvisingStatus
from apps.advising.thesis import topic_of
from apps.meetings.models import Meeting

from .meetings import meetings_block, upcoming_meetings
from .upcoming import upcoming_activities

ATTENTION_LIMIT = 5


def _attention_advisees(teacher: Teacher, today) -> dict:
    """RN38: the advisees with a late or an in-review activity, the most late first.

    Deleted and archived activities count for nothing (RN35). Informative only: nothing changes.
    """
    live = Q(activities__deleted_at__isnull=True)
    rows = list(
        Advising.objects.filter(teacher=teacher, status=AdvisingStatus.ACTIVE)
        .annotate(
            overdue_count=Count(
                "activities", filter=live & overdue_condition(today, "activities__")
            ),
            in_review_count=Count(
                "activities", filter=live & Q(activities__status=ActivityStatus.IN_REVIEW)
            ),
        )
        .filter(Q(overdue_count__gt=0) | Q(in_review_count__gt=0))
        .select_related("student__user", "thesis_topic")
        .order_by("-overdue_count", "-in_review_count", Lower("student__user__full_name"), "id")[
            : ATTENTION_LIMIT + 1
        ]
    )
    items = []
    for advising in rows[:ATTENTION_LIMIT]:
        topic = topic_of(advising)
        items.append(
            {
                "id": advising.student.user_id,
                "full_name": advising.student.user.full_name,
                "thesis_topic": topic.description if topic else None,
                "overdue_count": advising.overdue_count,
                "in_review_count": advising.in_review_count,
            }
        )
    return {"items": items, "has_more": len(rows) > ATTENTION_LIMIT}


def _next_meetings(teacher: Teacher, now: datetime) -> dict:
    """The accepted future meetings of the active advisings, with the advisee as the other party."""
    meetings = Meeting.objects.filter(
        advising__teacher=teacher, advising__status=AdvisingStatus.ACTIVE
    ).select_related("advising__student__user")
    rows = upcoming_meetings(meetings, now)
    return meetings_block(
        rows,
        lambda meeting: {
            "student": {
                "id": meeting.advising.student.user_id,
                "full_name": meeting.advising.student.user.full_name,
            }
        },
    )


def teacher_dashboard(teacher: Teacher) -> dict:
    """The teacher's summary of the active advisings (RF51), from one clock (RN24, RN37).

    Without an active advising the response is empty: no indicator that suggests advisees or
    activities that do not exist.
    """
    now = timezone.now()
    today = timezone.localdate(now)
    advisees_count = Advising.objects.filter(teacher=teacher, status=AdvisingStatus.ACTIVE).count()
    if advisees_count == 0:
        return {
            "advisees_count": 0,
            "activities": None,
            "attention_advisees": None,
            "next_deadlines": None,
            "next_meetings": None,
        }
    activities = counted_activities().filter(
        advising__teacher=teacher, advising__status=AdvisingStatus.ACTIVE
    )
    counts = count_activities(activities, today)
    return {
        "advisees_count": advisees_count,
        "activities": counts.as_indicators(),
        "attention_advisees": _attention_advisees(teacher, today),
        "next_deadlines": upcoming_activities(
            activities.select_related("advising__student__user"), today
        ),
        "next_meetings": _next_meetings(teacher, now),
    }
