from bisect import bisect_left

from django.db.models import Max, Prefetch
from ninja.errors import HttpError

from apps.accounts.models import User

from .models import (
    Activity,
    ActivityStatus,
    Comment,
    Review,
    ReviewFile,
    StatusChange,
    Submission,
    SubmissionFile,
)
from .queries import ACTIVITY_NOT_FOUND_MESSAGE, visible_activities

DEFAULT_PAGE_SIZE = 10


def _author(user: User) -> dict:
    return {"id": user.id, "full_name": user.full_name, "type": user.type}


def _file(stored_file: SubmissionFile | ReviewFile) -> dict:
    return {
        "id": stored_file.id,
        "file_name": stored_file.file_name,
        "size_bytes": stored_file.size_bytes,
        "content_type": stored_file.content_type,
    }


def activity_timeline(user: User, activity_id: int, before: int | None, limit: int) -> dict:
    """A page of the timeline of an activity the user may see (RN49), latest event first.

    The indexes of an activity are contiguous, from 1 (the creation) to `last_event_index`, so a
    page is a range of indexes: the `limit` indexes below `before` (or below the latest), and the
    records in that range are loaded from each source, with the number of queries fixed.
    """
    activity = (
        visible_activities(user)
        .filter(pk=activity_id)
        .select_related("advising__student__user", "advising__teacher__user")
        .first()
    )
    if activity is None:
        raise HttpError(404, ACTIVITY_NOT_FOUND_MESSAGE)
    top = (
        activity.last_event_index if before is None else min(before - 1, activity.last_event_index)
    )
    low = max(1, top - limit + 1)
    items = _events_between(activity, low, top) if top >= 1 else []
    return {
        "items": sorted(items, key=lambda event: event["index"], reverse=True),
        "count": activity.last_event_index,
        "has_more": top >= 1 and low > 1,
    }


def _events_between(activity: Activity, low: int, top: int) -> list[dict]:
    teacher = activity.advising.teacher.user
    student = activity.advising.student.user
    in_range = {"activity": activity, "index__gte": low, "index__lte": top}
    deliveries = {
        index: version
        for version, index in enumerate(
            Submission.objects.filter(activity=activity)
            .order_by("index")
            .values_list("index", flat=True),
            start=1,
        )
    }
    delivery_indexes = sorted(deliveries)
    last_review = Review.objects.filter(activity=activity).aggregate(last=Max("index"))["last"]
    events: list[dict] = []
    if low <= 1 <= top:
        events.append(
            {
                "type": "created",
                "index": 1,
                "id": activity.id,
                "occurred_at": activity.created_at,
                "author": _author(teacher),
            }
        )
    for submission in Submission.objects.filter(**in_range).prefetch_related(
        Prefetch("files", queryset=SubmissionFile.objects.order_by("id"))
    ):
        is_latest = submission.index == delivery_indexes[-1]
        events.append(
            {
                "type": "delivery",
                "index": submission.index,
                "id": submission.id,
                "occurred_at": submission.submitted_at,
                "author": _author(student),
                "version": deliveries[submission.index],
                "files": [_file(stored) for stored in submission.files.all()],
                "awaiting_review": activity.status == ActivityStatus.IN_REVIEW
                and is_latest
                and (last_review is None or last_review < submission.index),
            }
        )
    for change in StatusChange.objects.filter(**in_range).select_related("author"):
        event = {
            "type": "sent_for_review"
            if change.status == ActivityStatus.IN_REVIEW
            else "status_change",
            "index": change.index,
            "id": change.id,
            "occurred_at": change.changed_at,
            "author": _author(change.author),
        }
        if change.status != ActivityStatus.IN_REVIEW:
            event["status"] = change.status
        events.append(event)
    for comment in Comment.objects.filter(**in_range).select_related("author"):
        events.append(
            {
                "type": "comment",
                "index": comment.index,
                "id": comment.id,
                "occurred_at": comment.created_at,
                "author": _author(comment.author),
                "text": comment.text,
            }
        )
    for review in Review.objects.filter(**in_range).prefetch_related(
        Prefetch("files", queryset=ReviewFile.objects.order_by("id"))
    ):
        before_it = bisect_left(delivery_indexes, review.index)
        events.append(
            {
                "type": "review",
                "index": review.index,
                "id": review.id,
                "occurred_at": review.decided_at,
                "author": _author(teacher),
                "decision": review.decision,
                "justification": review.justification,
                "version": before_it or None,
                "files": [_file(stored) for stored in review.files.all()],
            }
        )
    return events
