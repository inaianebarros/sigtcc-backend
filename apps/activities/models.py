from django.conf import settings
from django.db import models
from django.db.models import F, Q

from apps.advising.models import Advising

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024


def take_next_event_index(activity_id: int) -> int:
    """The next index of the activity's timeline, taken in the database (RN49).

    The UPDATE locks the activity row until the transaction ends, so simultaneous actions queue
    up and never take the same index, and an action that fails and rolls back gives its index back:
    the indexes of an activity have no gap and no repeat.
    """
    Activity.objects.filter(pk=activity_id).update(last_event_index=F("last_event_index") + 1)
    return Activity.objects.values_list("last_event_index", flat=True).get(pk=activity_id)


class ActivityStatus(models.TextChoices):
    PENDING = "pending", "Pendente"
    IN_PROGRESS = "in_progress", "Em andamento"
    IN_REVIEW = "in_review", "Em revisão"
    COMPLETED = "completed", "Concluída"
    ARCHIVED = "archived", "Arquivada"


class ReviewDecision(models.TextChoices):
    ACCEPTED = "accepted", "Aceita"
    REJECTED = "rejected", "Recusada"


class Activity(models.Model):
    advising = models.ForeignKey(Advising, on_delete=models.PROTECT, related_name="activities")
    title = models.CharField(max_length=200)
    description = models.TextField()
    deadline = models.DateField()
    status = models.CharField(
        max_length=12, choices=ActivityStatus.choices, default=ActivityStatus.PENDING
    )
    created_at = models.DateTimeField(auto_now_add=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    #: The index of the last event of the activity's timeline (RN49). The creation is event 1, and
    #: each action takes the next one, under the lock the actions already hold on the activity.
    last_event_index = models.PositiveIntegerField(default=1)


class TimelineEvent(models.Model):
    """An event of an activity's timeline (RN49): a delivery, a review, a comment or a status change.

    The index is taken from the activity when the event is created, so any code that creates one
    (the services, the seed, the admin) gets the next index of that activity. It is never edited.
    """

    index = models.PositiveIntegerField(editable=False)

    class Meta:
        abstract = True
        constraints = [
            models.UniqueConstraint(
                fields=["activity", "index"], name="%(class)s_index_unique_in_activity"
            ),
        ]

    def save(self, *args, **kwargs):
        if self._state.adding and self.index is None:
            self.index = take_next_event_index(self.activity_id)
        super().save(*args, **kwargs)


class Submission(TimelineEvent):
    activity = models.ForeignKey(Activity, on_delete=models.PROTECT, related_name="submissions")
    submitted_at = models.DateTimeField(auto_now_add=True)


class SubmissionFile(models.Model):
    submission = models.ForeignKey(Submission, on_delete=models.PROTECT, related_name="files")
    file_name = models.CharField(max_length=255)
    size_bytes = models.BigIntegerField()
    content_type = models.CharField(max_length=100)
    storage_key = models.CharField(max_length=500, unique=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(size_bytes__gte=1, size_bytes__lte=MAX_FILE_SIZE_BYTES),
                name="submission_file_size_within_limit",
            ),
        ]


class Review(TimelineEvent):
    activity = models.ForeignKey(Activity, on_delete=models.PROTECT, related_name="reviews")
    decision = models.CharField(max_length=10, choices=ReviewDecision.choices)
    justification = models.TextField(blank=True, default="")
    decided_at = models.DateTimeField(auto_now_add=True)


class ReviewFile(models.Model):
    """A file the advisor attaches to a refusal (RN48): part of the decision, never changed after."""

    review = models.ForeignKey(Review, on_delete=models.PROTECT, related_name="files")
    file_name = models.CharField(max_length=255)
    size_bytes = models.BigIntegerField()
    content_type = models.CharField(max_length=100)
    storage_key = models.CharField(max_length=500, unique=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(size_bytes__gte=1, size_bytes__lte=MAX_FILE_SIZE_BYTES),
                name="review_file_size_within_limit",
            ),
        ]


class Comment(TimelineEvent):
    activity = models.ForeignKey(Activity, on_delete=models.PROTECT, related_name="comments")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="comments"
    )
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)


class StatusChangeStatus(models.TextChoices):
    PENDING = ActivityStatus.PENDING.value, ActivityStatus.PENDING.label
    IN_PROGRESS = ActivityStatus.IN_PROGRESS.value, ActivityStatus.IN_PROGRESS.label
    IN_REVIEW = ActivityStatus.IN_REVIEW.value, ActivityStatus.IN_REVIEW.label


class StatusChange(TimelineEvent):
    """A change of status made by the student: pending, in progress or sent for review (RN49).

    Accepting and refusing are reviews, so they are not recorded here, and the archiving by the
    system is not an event of the timeline.
    """

    activity = models.ForeignKey(Activity, on_delete=models.PROTECT, related_name="status_changes")
    status = models.CharField(max_length=12, choices=StatusChangeStatus.choices)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="status_changes"
    )
    changed_at = models.DateTimeField(auto_now_add=True)
