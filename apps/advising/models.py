from django.db import models
from django.db.models import Q

from apps.accounts.models import Student, Teacher


class AdvisingRequestStatus(models.TextChoices):
    PENDING = "pending", "Pendente"
    ACCEPTED = "accepted", "Aceita"
    REJECTED = "rejected", "Recusada"
    UNANSWERED = "unanswered", "Não respondida"


class AdvisingStatus(models.TextChoices):
    ACTIVE = "active", "Ativa"
    ENDED = "ended", "Encerrada"


class AdvisingRequest(models.Model):
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="advising_requests")
    teacher = models.ForeignKey(Teacher, on_delete=models.PROTECT, related_name="advising_requests")
    status = models.CharField(
        max_length=10, choices=AdvisingRequestStatus.choices, default=AdvisingRequestStatus.PENDING
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["student"],
                condition=Q(status="pending"),
                name="advising_request_one_pending_per_student",
            ),
        ]


class Advising(models.Model):
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="advisings")
    teacher = models.ForeignKey(Teacher, on_delete=models.PROTECT, related_name="advisings")
    request = models.OneToOneField(
        AdvisingRequest, null=True, blank=True, on_delete=models.PROTECT, related_name="advising"
    )
    status = models.CharField(
        max_length=10, choices=AdvisingStatus.choices, default=AdvisingStatus.ACTIVE
    )
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["student"],
                condition=Q(status="active"),
                name="advising_one_active_per_student",
            ),
            models.CheckConstraint(
                condition=(Q(status="ended", ended_at__isnull=False))
                | (Q(status="active", ended_at__isnull=True)),
                name="advising_end_date_matches_status",
            ),
        ]


class ThesisTopic(models.Model):
    student = models.OneToOneField(Student, on_delete=models.PROTECT, related_name="thesis_topic")
    description = models.TextField()
    updated_at = models.DateTimeField(auto_now=True)
