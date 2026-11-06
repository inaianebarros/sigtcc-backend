from datetime import datetime
from enum import StrEnum

from django.db import models
from django.db.models import Case, CharField, F, Q, Value, When

from apps.accounts.models import Student, Teacher

from .deadlines import answer_cutoff


class AdvisingRequestStatus(models.TextChoices):
    PENDING = "pending", "Pendente"
    ACCEPTED = "accepted", "Aceita"
    REJECTED = "rejected", "Recusada"
    UNANSWERED = "unanswered", "Não respondida"


ANSWERED_STATUSES = (AdvisingRequestStatus.ACCEPTED, AdvisingRequestStatus.REJECTED)


class AnswerAction(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"


class BlockedReason(StrEnum):
    ACTIVE_ADVISING = "active_advising"
    PENDING_REQUEST = "pending_request"


class AdvisingStatus(models.TextChoices):
    ACTIVE = "active", "Ativa"
    ENDED = "ended", "Encerrada"


class AdvisingRequestQuerySet(models.QuerySet):
    """Filters take an optional cutoff so that one read can decide every rule with one clock."""

    def overdue_pending(self, cutoff: datetime | None = None):
        return self.filter(_overdue_pending_condition(cutoff))

    def valid_pending(self, cutoff: datetime | None = None):
        if cutoff is None:
            cutoff = answer_cutoff()
        return self.filter(status=AdvisingRequestStatus.PENDING, requested_at__gt=cutoff)

    def with_effective_status(self, cutoff: datetime | None = None):
        return self.annotate(
            effective_status=Case(
                When(
                    _overdue_pending_condition(cutoff),
                    then=Value(AdvisingRequestStatus.UNANSWERED),
                ),
                default=F("status"),
                output_field=CharField(),
            )
        )


def _overdue_pending_condition(cutoff: datetime | None = None) -> Q:
    if cutoff is None:
        cutoff = answer_cutoff()
    return Q(status=AdvisingRequestStatus.PENDING, requested_at__lte=cutoff)


class AdvisingRequest(models.Model):
    objects = AdvisingRequestQuerySet.as_manager()

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
                condition=Q(status=AdvisingRequestStatus.PENDING),
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
                condition=Q(status=AdvisingStatus.ACTIVE),
                name="advising_one_active_per_student",
            ),
            models.CheckConstraint(
                condition=Q(status=AdvisingStatus.ENDED, ended_at__isnull=False)
                | Q(status=AdvisingStatus.ACTIVE, ended_at__isnull=True),
                name="advising_end_date_matches_status",
            ),
        ]


class ThesisTopic(models.Model):
    student = models.OneToOneField(Student, on_delete=models.PROTECT, related_name="thesis_topic")
    description = models.TextField()
    updated_at = models.DateTimeField(auto_now=True)
