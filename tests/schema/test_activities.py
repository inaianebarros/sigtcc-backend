import pytest
from django.db import IntegrityError, transaction

from apps.activities.models import (
    ActivityStatus,
    Comment,
    Review,
    ReviewDecision,
    Submission,
    SubmissionFile,
)
from tests.factories import make_activity

pytestmark = pytest.mark.django_db

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024


def make_submission():
    return Submission.objects.create(activity=make_activity())


def create_file(submission, size_bytes, storage_key="k/1"):
    return SubmissionFile.objects.create(
        submission=submission,
        file_name="capitulo2.pdf",
        size_bytes=size_bytes,
        content_type="application/pdf",
        storage_key=storage_key,
    )


def test_new_activity_starts_pending_and_not_deleted():
    activity = make_activity()

    assert activity.status == ActivityStatus.PENDING
    assert activity.deleted_at is None


def test_submission_file_accepts_sizes_from_one_byte_up_to_ten_megabytes():
    submission = make_submission()

    create_file(submission, 1, storage_key="k/min")
    create_file(submission, MAX_FILE_SIZE_BYTES, storage_key="k/max")


@pytest.mark.parametrize("size_bytes", [0, MAX_FILE_SIZE_BYTES + 1])
def test_submission_file_rejects_sizes_outside_the_limit(size_bytes):
    submission = make_submission()

    with pytest.raises(IntegrityError), transaction.atomic():
        create_file(submission, size_bytes)


def test_submission_file_storage_key_must_be_unique():
    submission = make_submission()
    create_file(submission, 10, storage_key="same-key")

    with pytest.raises(IntegrityError), transaction.atomic():
        create_file(submission, 10, storage_key="same-key")


def test_review_and_comment_attach_to_an_activity():
    activity = make_activity()

    Review.objects.create(activity=activity, decision=ReviewDecision.ACCEPTED)
    Comment.objects.create(
        activity=activity, author=activity.advising.teacher.user, text="Bom trabalho"
    )

    assert activity.reviews.get().decision == ReviewDecision.ACCEPTED
    assert activity.comments.get().text == "Bom trabalho"
