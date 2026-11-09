from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.advising.models import Advising

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024


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


class Submission(models.Model):
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


class Review(models.Model):
    activity = models.ForeignKey(Activity, on_delete=models.PROTECT, related_name="reviews")
    decision = models.CharField(max_length=10, choices=ReviewDecision.choices)
    justification = models.TextField(blank=True, default="")
    decided_at = models.DateTimeField(auto_now_add=True)


class Comment(models.Model):
    activity = models.ForeignKey(Activity, on_delete=models.PROTECT, related_name="comments")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="comments"
    )
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
