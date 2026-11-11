"""The e-mail of a notification (RF54): the label of the type as subject, the same text the system
shows as body, and a link to the system. Plain text, and nothing the notification does not show."""

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import F

from .messages import message_for
from .models import Notification, NotificationType

MAX_EMAIL_ATTEMPTS = 5

logger = logging.getLogger("sigtcc.notifications")


def pending_notification_ids() -> list[int]:
    """The ids of the notifications still to be e-mailed, oldest first."""
    return list(
        Notification.objects.filter(
            email_sent_at__isnull=True, email_attempts__lt=MAX_EMAIL_ATTEMPTS
        )
        .order_by("id")
        .values_list("id", flat=True)
    )


def lock_pending_notification(notification_id: int) -> Notification | None:
    """Locks the notification for the time of its sending, or returns None when another run holds
    it or it is no longer pending: that run has it, so this one must not send it a second time.
    Call it inside a transaction."""
    return (
        Notification.objects.select_for_update(skip_locked=True, of=("self",))
        .select_related("recipient")
        .filter(
            pk=notification_id,
            email_sent_at__isnull=True,
            email_attempts__lt=MAX_EMAIL_ATTEMPTS,
        )
        .first()
    )


def email_for(notification: Notification) -> tuple[str, str]:
    subject = NotificationType(notification.type).label
    text = message_for(notification.type, notification.payload)
    return subject, f"{text}\n\nAcesse o SIGTCC: {settings.FRONTEND_URL}"


def send_notification_email(notification: Notification) -> None:
    subject, body = email_for(notification)
    send_mail(subject, body, None, [notification.recipient.email])


def record_failed_attempt(notification_id: int) -> None:
    """Counts a failed attempt; at the last one the notification is given up and it is logged."""
    # Only a notification still pending counts: another run may have sent it after this one failed.
    counted = Notification.objects.filter(pk=notification_id, email_sent_at__isnull=True).update(
        email_attempts=F("email_attempts") + 1
    )
    if not counted:
        return
    attempts = Notification.objects.values_list("email_attempts", flat=True).get(pk=notification_id)
    if attempts >= MAX_EMAIL_ATTEMPTS:
        logger.error(
            "gave up e-mailing notification %s after %s attempts", notification_id, attempts
        )
