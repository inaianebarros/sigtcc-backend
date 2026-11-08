from dataclasses import dataclass
from datetime import date

from django.db import transaction
from django.utils import timezone
from ninja.errors import HttpError

from apps.accounts.models import Teacher
from apps.advising.linked_student_profile import STUDENT_NOT_FOUND_MESSAGE
from apps.advising.models import Advising, AdvisingStatus
from config.errors import ConflictError, FieldValidationError

from .models import Activity, ActivityStatus
from .queries import ACTIVITY_NOT_FOUND_MESSAGE

PAST_DEADLINE_MESSAGE = "O prazo não pode ser anterior a hoje."
COMPLETED_MESSAGE = "Esta atividade já foi concluída e não pode ser alterada."
EDITABLE_FIELDS = ("title", "description", "deadline")


@dataclass(frozen=True)
class FieldChange:
    old: str | date
    new: str | date


@dataclass(frozen=True)
class ActivityEdit:
    activity: Activity
    changes: dict[str, FieldChange]


@transaction.atomic
def create_activity(
    teacher: Teacher, student_id: int, title: str, description: str, deadline: date
) -> Activity:
    """Creates an activity for the active advising between the teacher and the student (RN13).

    The advising row is locked by the same query that checks it is active (ADR 0004), so an
    advising that ends meanwhile is answered with the same 404 and nothing is created.
    """
    advising = (
        Advising.objects.select_for_update()
        .filter(student__user_id=student_id, teacher=teacher, status=AdvisingStatus.ACTIVE)
        .first()
    )
    if advising is None:
        raise HttpError(404, STUDENT_NOT_FOUND_MESSAGE)
    return Activity.objects.create(
        advising=advising, title=title, description=description, deadline=deadline
    )


def _lock_activity_of(teacher: Teacher, activity_id: int) -> Activity:
    """The activity of an advising the teacher advises now, with the advising row locked (RN14).

    The same query locks the advising and checks that it is active (ADR 0004). Another
    teacher, a deleted or missing activity and an ended advising all answer the same 404, and a
    completed activity cannot change anymore (RN15).
    """
    advising = (
        Advising.objects.select_for_update(of=("self",))
        .filter(
            teacher=teacher,
            status=AdvisingStatus.ACTIVE,
            activities__pk=activity_id,
            activities__deleted_at__isnull=True,
        )
        .first()
    )
    if advising is None:
        raise HttpError(404, ACTIVITY_NOT_FOUND_MESSAGE)
    activity = Activity.objects.get(pk=activity_id)
    if activity.status == ActivityStatus.COMPLETED:
        raise ConflictError(COMPLETED_MESSAGE)
    return activity


@transaction.atomic
def edit_activity(
    teacher: Teacher,
    activity_id: int,
    title: str | None = None,
    description: str | None = None,
    deadline: date | None = None,
) -> ActivityEdit:
    """Changes only the given fields and reports what changed (RF26).

    Only a deadline that changed must be today or later, so the title of a late activity can
    still be fixed. An edit that changes nothing writes nothing and reports no change.
    """
    activity = _lock_activity_of(teacher, activity_id)
    given = {"title": title, "description": description, "deadline": deadline}
    changes = {
        field: FieldChange(old=getattr(activity, field), new=value)
        for field, value in given.items()
        if value is not None and value != getattr(activity, field)
    }
    if "deadline" in changes and deadline < timezone.localdate():
        raise FieldValidationError({"deadline": PAST_DEADLINE_MESSAGE})
    if changes:
        for field, change in changes.items():
            setattr(activity, field, change.new)
        activity.save(update_fields=list(changes))
    return ActivityEdit(activity=activity, changes=changes)


@transaction.atomic
def delete_activity(teacher: Teacher, activity_id: int) -> Activity:
    """Soft delete (RF27): submissions, reviews and comments stay; there is no undo."""
    activity = _lock_activity_of(teacher, activity_id)
    activity.deleted_at = timezone.now()
    activity.save(update_fields=["deleted_at"])
    return activity
