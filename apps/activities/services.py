import logging
from dataclasses import dataclass
from datetime import date
from typing import IO

from django.db import transaction
from django.utils import timezone
from ninja.errors import HttpError

from apps.accounts.models import Teacher
from apps.advising.linked_student_profile import STUDENT_NOT_FOUND_MESSAGE
from apps.advising.models import Advising, AdvisingStatus
from config.changes import FieldChange
from config.errors import ConflictError, FieldValidationError

from .models import Activity, ActivityStatus, Submission, SubmissionFile
from .queries import (
    ACTIVITY_NOT_FOUND_MESSAGE,
    FILE_NOT_FOUND_MESSAGE,
    submission_file_for_download,
)
from .storage import new_storage_key, submission_storage
from .uploads import check_files

PAST_DEADLINE_MESSAGE = "O prazo não pode ser anterior a hoje."
COMPLETED_MESSAGE = "Esta atividade já foi concluída e não pode ser alterada."
STORAGE_UNAVAILABLE_MESSAGE = "Não foi possível guardar os arquivos. Tente novamente."
DOWNLOAD_UNAVAILABLE_MESSAGE = "Não foi possível obter o arquivo. Tente novamente."
DELIVERY_REFUSED_MESSAGES = {
    ActivityStatus.IN_REVIEW: "Esta atividade está em revisão e não aceita novas entregas.",
    ActivityStatus.COMPLETED: "Esta atividade já foi concluída e não aceita novas entregas.",
}
EDITABLE_FIELDS = ("title", "description", "deadline")

logger = logging.getLogger(__name__)


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


def _lock_changeable_activity(teacher: Teacher, activity_id: int) -> Activity:
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
    activity = _lock_changeable_activity(teacher, activity_id)
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
    activity = _lock_changeable_activity(teacher, activity_id)
    activity.deleted_at = timezone.now()
    activity.save(update_fields=["deleted_at"])
    return activity


def _lock_deliverable_activity(student, activity_id: int) -> Activity:
    """The activity of the student's active advising, with the advising and the activity locked.

    The same query locks the advising and checks that it is active (ADR 0004). Another student,
    a deleted, archived or missing activity and an ended advising all answer the same 404. Only a
    pending or in-progress activity takes a delivery (RN16).
    """
    advising = (
        Advising.objects.select_for_update(of=("self",))
        .filter(
            student=student,
            status=AdvisingStatus.ACTIVE,
            activities__pk=activity_id,
            activities__deleted_at__isnull=True,
        )
        .first()
    )
    if advising is None:
        raise HttpError(404, ACTIVITY_NOT_FOUND_MESSAGE)
    activity = Activity.objects.select_for_update().get(pk=activity_id)
    if activity.status == ActivityStatus.ARCHIVED:
        raise HttpError(404, ACTIVITY_NOT_FOUND_MESSAGE)
    if activity.status in DELIVERY_REFUSED_MESSAGES:
        raise ConflictError(DELIVERY_REFUSED_MESSAGES[activity.status])
    return activity


def _save_to_storage(storage, activity_id: int, file) -> str:
    try:
        return storage.save(new_storage_key(activity_id), file)
    except Exception as error:
        logger.exception("The storage refused a delivered file")
        raise HttpError(503, STORAGE_UNAVAILABLE_MESSAGE) from error


def _remove_stored(storage, keys: list[str]) -> None:
    """Best effort: a file that cannot be removed is logged and never hides the original error."""
    for key in keys:
        try:
            storage.delete(key)
        except Exception:
            logger.exception("Could not remove the stored file %s after a failed delivery", key)


def deliver_submission(student, activity_id: int, files) -> Submission:
    """Records a delivery of 1 to 5 files for an activity (RF31), all or nothing.

    The files are checked before anything is looked up or stored, so one bad file rejects the
    whole delivery. They are stored while the advising and the activity are locked (ADR 0004). If
    anything fails, even the commit, the files already stored are removed, and a storage failure
    leaves no record in the database.
    """
    checked_files = check_files(files)
    storage = submission_storage()
    stored_keys: list[str] = []
    try:
        with transaction.atomic():
            activity = _lock_deliverable_activity(student, activity_id)
            submission = Submission.objects.create(activity=activity)
            records = []
            for file, checked in zip(files, checked_files, strict=True):
                stored_keys.append(_save_to_storage(storage, activity.id, file))
                records.append(
                    SubmissionFile(
                        submission=submission,
                        file_name=checked.file_name,
                        size_bytes=file.size,
                        content_type=checked.content_type,
                        storage_key=stored_keys[-1],
                    )
                )
            SubmissionFile.objects.bulk_create(records)
    except BaseException:
        # BaseException on purpose: whatever stops the delivery, the stored files must go.
        _remove_stored(storage, stored_keys)
        raise
    return submission


def open_submission_file(user, activity_id: int, file_id: int) -> tuple[SubmissionFile, IO[bytes]]:
    """The file of an activity the user may see, opened for reading (RF31).

    A file missing from the storage answers the same 404 as one the user may not see, and a
    storage that cannot be reached answers a 503, like the delivery does.
    """
    submission_file = submission_file_for_download(user, activity_id, file_id)
    try:
        return submission_file, submission_storage().open(submission_file.storage_key, "rb")
    except FileNotFoundError as error:
        raise HttpError(404, FILE_NOT_FOUND_MESSAGE) from error
    except Exception as error:
        logger.exception("The storage could not open the file %s", submission_file.storage_key)
        raise HttpError(503, DOWNLOAD_UNAVAILABLE_MESSAGE) from error
