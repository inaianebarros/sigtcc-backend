from django.conf import settings
from django.db import models
from django.db.models import F, Q

from apps.advising.models import Advising


class MeetingStatus(models.TextChoices):
    PENDING = "pending", "Pendente"
    ACCEPTED = "accepted", "Aceita"
    REJECTED = "rejected", "Recusada"
    CANCELLED = "cancelled", "Cancelada"


SYSTEM_CANCELLATION_REASON = (
    "Esta reunião foi cancelada automaticamente pelo sistema porque a orientação foi encerrada."
)


class Meeting(models.Model):
    advising = models.ForeignKey(Advising, on_delete=models.PROTECT, related_name="meetings")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="meetings"
    )
    scheduled_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    description = models.TextField()
    status = models.CharField(
        max_length=10, choices=MeetingStatus.choices, default=MeetingStatus.PENDING
    )
    rejection_reason = models.TextField(blank=True, default="")
    cancellation_reason = models.TextField(blank=True, default="")
    cancelled_by_system = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__gt=F("scheduled_at")),
                name="meeting_ends_after_it_starts",
            ),
            models.CheckConstraint(
                condition=~Q(status=MeetingStatus.REJECTED) | ~Q(rejection_reason=""),
                name="meeting_rejection_requires_reason",
            ),
            models.CheckConstraint(
                condition=~Q(cancelled_by_system=True)
                | (Q(status=MeetingStatus.CANCELLED) & ~Q(cancellation_reason="")),
                name="meeting_system_cancellation_requires_reason",
            ),
            models.CheckConstraint(
                condition=Q(status=MeetingStatus.CANCELLED)
                | Q(cancellation_reason="", cancelled_by_system=False),
                name="meeting_cancellation_reason_only_when_cancelled",
            ),
        ]
