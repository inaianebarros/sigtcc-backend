from datetime import date

from django.db.models import Case, Count, F, Prefetch, Q, QuerySet, When
from django.utils import timezone
from ninja.errors import HttpError

from apps.accounts.models import User, UserType
from apps.advising.models import AdvisingStatus

from .lateness import overdue_condition, with_overdue
from .models import Activity, ActivityStatus, Comment, Review, Submission, SubmissionFile
from .progress import ActivityCounts, count_activities

ACTIVITY_NOT_FOUND_MESSAGE = "Atividade não encontrada."
FILE_NOT_FOUND_MESSAGE = "Arquivo não encontrado."


def visible_activities(user: User) -> QuerySet[Activity]:
    """Activities the user may see: not deleted, not archived, in an active advising (RN07)."""
    scope = (
        {"advising__student": user.student}
        if user.type == UserType.STUDENT
        else {"advising__teacher": user.teacher}
    )
    return Activity.objects.filter(
        advising__status=AdvisingStatus.ACTIVE, deleted_at__isnull=True, **scope
    ).exclude(status=ActivityStatus.ARCHIVED)


def activities_summary(user: User, today: date | None = None) -> ActivityCounts:
    """The general numbers of the activities screen: the same count and scope as the dashboards.

    It counts the activities the user may see (RN35) and takes no filter, so the search and the
    filters of the list never change it.
    """
    return count_activities(visible_activities(user), today or timezone.localdate())


def list_activities(
    user: User,
    status: str | None,
    overdue: bool | None,
    student_id: int | None,
    today: date | None = None,
) -> QuerySet[Activity]:
    activities = visible_activities(user)
    if status is not None:
        activities = activities.filter(status=status)
    if overdue is not None:
        condition = overdue_condition(today)
        activities = activities.filter(condition) if overdue else activities.exclude(condition)
    if student_id is not None:
        activities = activities.filter(advising__student__user_id=student_id)
    is_completed = Q(status=ActivityStatus.COMPLETED)
    return (
        with_overdue(activities, today)
        .select_related("advising__student__user")
        .annotate(
            open_deadline=Case(When(~is_completed, then=F("deadline"))),
            done_deadline=Case(When(is_completed, then=F("deadline"))),
        )
        .order_by(
            F("open_deadline").asc(nulls_last=True),
            F("done_deadline").desc(nulls_last=True),
            "id",
        )
    )


def activity_detail(user: User, activity_id: int, today: date | None = None) -> Activity:
    activity = (
        with_overdue(visible_activities(user).filter(pk=activity_id), today)
        .select_related("advising__student__user", "advising__teacher__user")
        .annotate(comments_count=Count("comments", distinct=True))
        .prefetch_related(
            Prefetch(
                "submissions",
                queryset=Submission.objects.order_by("submitted_at", "id").prefetch_related(
                    Prefetch("files", queryset=SubmissionFile.objects.order_by("id"))
                ),
                to_attr="ordered_submissions",
            ),
            Prefetch(
                "reviews",
                queryset=Review.objects.order_by("decided_at", "id"),
                to_attr="ordered_reviews",
            ),
        )
        .first()
    )
    if activity is None:
        raise HttpError(404, ACTIVITY_NOT_FOUND_MESSAGE)
    return activity


def submission_file_for_download(user: User, activity_id: int, file_id: int) -> SubmissionFile:
    """The file of an activity the user may see: its student and the active advisor (RF31).

    Anyone else, a deleted, archived or ended-advising activity, a missing file and a file of
    another activity all answer the same 404.
    """
    submission_file = (
        SubmissionFile.objects.filter(
            pk=file_id,
            submission__activity__in=visible_activities(user).filter(pk=activity_id),
        )
        .select_related("submission")
        .first()
    )
    if submission_file is None:
        raise HttpError(404, FILE_NOT_FOUND_MESSAGE)
    return submission_file


def activity_item(activity_id: int) -> Activity:
    """One activity in the shape of a list item: with `overdue` and the student loaded."""
    return (
        with_overdue(Activity.objects.filter(pk=activity_id))
        .select_related("advising__student__user")
        .get()
    )


def list_comments(user: User, activity_id: int) -> QuerySet[Comment]:
    """The comments of an activity the user may see, oldest first, with the authors loaded (RF32).

    Reading works in any visible status, completed included. The scope is the one of the detail.
    """
    if not visible_activities(user).filter(pk=activity_id).exists():
        raise HttpError(404, ACTIVITY_NOT_FOUND_MESSAGE)
    return (
        Comment.objects.filter(activity_id=activity_id)
        .select_related("author")
        .order_by("created_at", "id")
    )
