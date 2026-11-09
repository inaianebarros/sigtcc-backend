from django.conf import settings
from django.db import models

ADVISING_REQUEST_REFERENCE = "advising_request"
ADVISING_REFERENCE = "advising"
ACTIVITY_REFERENCE = "activity"
MEETING_REFERENCE = "meeting"


class NotificationType(models.TextChoices):
    ADVISING_REQUEST_RECEIVED = "advising_request_received", "Nova solicitação de orientação"
    ADVISING_REQUEST_ANSWERED = "advising_request_answered", "Solicitação de orientação respondida"
    ADVISING_REQUEST_UNANSWERED = (
        "advising_request_unanswered",
        "Solicitação de orientação não respondida",
    )
    ACTIVITY_CREATED = "activity_created", "Nova atividade"
    ACTIVITY_COMMENTED = "activity_commented", "Comentário em atividade"
    ACTIVITY_SUBMITTED = "activity_submitted", "Entrega de atividade"
    ACTIVITY_AWAITING_REVIEW = "activity_awaiting_review", "Atividade aguardando revisão"
    ACTIVITY_REVIEW_RESULT = "activity_review_result", "Resultado da revisão da atividade"
    MEETING_REQUESTED = "meeting_requested", "Reunião solicitada"
    MEETING_ANSWERED = "meeting_answered", "Reunião respondida"
    ACTIVITY_CHANGED = "activity_changed", "Atividade alterada"
    ACTIVITY_DELETED = "activity_deleted", "Atividade excluída"
    ADVISING_ENDED = "advising_ended", "Orientação encerrada"


class Notification(models.Model):
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="notifications"
    )
    type = models.CharField(max_length=40, choices=NotificationType.choices)
    reference_type = models.CharField(max_length=50, blank=True, default="")
    reference_id = models.BigIntegerField(null=True, blank=True)
    # A snapshot of the event when it happened (the activity may be edited or deleted later); the
    # structure of each type is documented in the notifications API.
    payload = models.JSONField(default=dict, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["recipient", "is_read", "created_at"], name="notification_inbox_idx"
            ),
        ]
