"""Removes the demonstration users and everything that belongs to them, so the data can be recreated.

The relations are `PROTECT`, so the removal is physical and in dependency order, in one
transaction: a failure in the middle undoes everything. It only touches the users with the
demonstration e-mail domain and what hangs from them; it refuses to run while a real user is tied
to one of them (a request or an advising), so no real data is ever deleted.
"""

import logging

from django.db import transaction
from django.db.models import Q, QuerySet

from apps.activities.models import (
    Activity,
    Comment,
    Review,
    ReviewFile,
    StatusChange,
    Submission,
    SubmissionFile,
)
from apps.activities.storage import submission_storage
from apps.advising.models import Advising, AdvisingRequest, ThesisTopic
from apps.meetings.models import Meeting
from apps.notifications.models import Notification

from .demo_seed import DEMO_DOMAIN, DemoSeedError
from .models import PublishedArticle, Student, Teacher, User

logger = logging.getLogger(__name__)


def demo_users() -> QuerySet[User]:
    return User.objects.filter(email__iendswith=f"@{DEMO_DOMAIN}")


def _is_demo(side: str) -> Q:
    return Q(**{f"{side}__user__email__iendswith": f"@{DEMO_DOMAIN}"})


def _demo_party() -> Q:
    return _is_demo("student") | _is_demo("teacher")


def _foreign_links() -> int:
    """Advisings and requests with a demonstration user on one side and a real user on the other."""
    mixed = (_is_demo("student") & ~_is_demo("teacher")) | (
        ~_is_demo("student") & _is_demo("teacher")
    )
    return Advising.objects.filter(mixed).count() + AdvisingRequest.objects.filter(mixed).count()


def _delete_activities(advisings: QuerySet[Advising]) -> list[str]:
    activities = Activity.objects.filter(advising__in=advisings)
    files = SubmissionFile.objects.filter(submission__activity__in=activities)
    review_files = ReviewFile.objects.filter(review__activity__in=activities)
    keys = [
        *files.values_list("storage_key", flat=True),
        *review_files.values_list("storage_key", flat=True),
    ]
    Comment.objects.filter(activity__in=activities).delete()
    StatusChange.objects.filter(activity__in=activities).delete()
    review_files.delete()
    Review.objects.filter(activity__in=activities).delete()
    files.delete()
    Submission.objects.filter(activity__in=activities).delete()
    activities.delete()
    return keys


def _delete_meetings(advisings: QuerySet[Advising]) -> None:
    Meeting.objects.filter(advising__in=advisings).delete()


def _delete_notifications(users: QuerySet[User]) -> None:
    Notification.objects.filter(recipient__in=users).delete()


def _delete_stored_files(keys: list[str]) -> None:
    """Best effort, after the commit: a file that cannot be removed is logged and never stops."""
    storage = submission_storage()
    for key in keys:
        try:
            storage.delete(key)
        except Exception:
            logger.exception("Could not remove the stored file %s of the demonstration data", key)


def ensure_no_real_user_is_tied() -> None:
    """Refuses while a real user has a request or an advising with a demonstration user."""
    if _foreign_links():
        raise DemoSeedError(
            "Há solicitações ou orientações ligando usuários reais a usuários de demonstração: "
            "nada foi apagado."
        )


def reset_demo_data() -> int:
    """Deletes the demonstration users and their data; returns how many users it removed."""
    ensure_no_real_user_is_tied()
    with transaction.atomic():
        users = demo_users()
        advisings = Advising.objects.filter(_demo_party())
        keys = _delete_activities(advisings)
        _delete_meetings(advisings)
        ThesisTopic.objects.filter(advising__in=advisings).delete()
        _delete_notifications(users)
        advisings.delete()
        AdvisingRequest.objects.filter(_demo_party()).delete()
        PublishedArticle.objects.filter(
            Q(student__user__in=users) | Q(teacher__user__in=users)
        ).delete()
        Student.objects.filter(user__in=users).delete()
        Teacher.objects.filter(user__in=users).delete()
        removed = users.count()
        users.delete()
    _delete_stored_files(keys)
    return removed
