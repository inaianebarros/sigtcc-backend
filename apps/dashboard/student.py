from dataclasses import asdict

from django.utils import timezone

from apps.accounts.models import Student
from apps.activities.lateness import with_overdue
from apps.activities.models import ActivityStatus
from apps.activities.progress import count_activities, counted_activities
from apps.advising.models import (
    Advising,
    AdvisingRequest,
    AdvisingRequestStatus,
    AdvisingStatus,
)
from apps.advising.schemas import TeacherSummaryOut
from apps.advising.thesis import topic_of

NEXT_ACTIVITIES_LIMIT = 5
OPEN_STATUSES = (ActivityStatus.PENDING, ActivityStatus.IN_PROGRESS, ActivityStatus.IN_REVIEW)
# An accepted request is the one that started an advising: it is not a "last request" to act on.
SHOWN_REQUEST_STATUSES = (
    AdvisingRequestStatus.PENDING,
    AdvisingRequestStatus.REJECTED,
    AdvisingRequestStatus.UNANSWERED,
)


def _last_request(student: Student) -> dict | None:
    """The latest request, if it is still pending, refused or unanswered (the 7 days of RN20)."""
    request = (
        AdvisingRequest.objects.filter(student=student)
        .with_effective_status()
        .select_related("teacher__user")
        .order_by("-requested_at", "-id")
        .first()
    )
    if request is None or request.effective_status not in SHOWN_REQUEST_STATUSES:
        return None
    return {
        "id": request.id,
        "status": request.effective_status,
        "requested_at": request.requested_at,
        "teacher": TeacherSummaryOut.from_teacher(request.teacher),
    }


def _next_activities(advising: Advising, today) -> dict:
    """The open activities by deadline, so the late ones come first; one extra says there is more."""
    open_activities = (
        with_overdue(counted_activities().filter(advising=advising), today)
        .filter(status__in=OPEN_STATUSES)
        .order_by("deadline", "id")[: NEXT_ACTIVITIES_LIMIT + 1]
    )
    items = list(open_activities)
    return {"items": items[:NEXT_ACTIVITIES_LIMIT], "has_more": len(items) > NEXT_ACTIVITIES_LIMIT}


def student_dashboard(student: Student) -> dict:
    """The student's summary (RF50): everything is computed here, from one clock (RN24, RN37).

    Only the active advising counts (RN35). Without one, the dashboard says so and shows the last
    request and whether there is a history of ended advisings (RF49).
    """
    today = timezone.localdate()
    advising = (
        Advising.objects.filter(student=student, status=AdvisingStatus.ACTIVE)
        .select_related("teacher__user", "thesis_topic")
        .first()
    )
    has_ended_advisings = Advising.objects.filter(
        student=student, status=AdvisingStatus.ENDED
    ).exists()
    if advising is None:
        return {
            "has_active_advising": False,
            "advising": None,
            "activities": None,
            "next_activities": None,
            "last_request": _last_request(student),
            "has_ended_advisings": has_ended_advisings,
        }
    topic = topic_of(advising)
    counts = count_activities(counted_activities().filter(advising=advising), today)
    return {
        "has_active_advising": True,
        "advising": {
            "teacher": TeacherSummaryOut.from_teacher(advising.teacher),
            "thesis_topic": topic.description if topic else None,
        },
        "activities": {**asdict(counts), "progress_percent": counts.progress_percent},
        "next_activities": _next_activities(advising, today),
        "last_request": None,
        "has_ended_advisings": has_ended_advisings,
    }
