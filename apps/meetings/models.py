from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.advising.models import Advising


class MeetingStatus(models.TextChoices):
    PENDING = "pending", "Pendente"
    ACCEPTED = "accepted", "Aceita"
    REJECTED = "rejected", "Recusada"
    CANCELLED = "cancelled", "Cancelada"


class Meeting(models.Model):
    advising = models.ForeignKey(Advising, on_delete=models.PROTECT, related_name="meetings")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="meetings"
    )
    scheduled_at = models.DateTimeField()
    description = models.TextField()
    status = models.CharField(
        max_length=10, choices=MeetingStatus.choices, default=MeetingStatus.PENDING
    )
    rejection_reason = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~Q(status="rejected") | ~Q(rejection_reason=""),
                name="meeting_rejection_requires_reason",
            ),
        ]
