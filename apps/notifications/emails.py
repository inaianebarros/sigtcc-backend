"""The e-mail of a notification (RF54): the label of the type as subject, the same text the system
shows as body, and a link to the system. Plain text, and nothing the notification does not show."""

from django.conf import settings
from django.core.mail import send_mail

from .messages import message_for
from .models import Notification, NotificationType

MAX_EMAIL_ATTEMPTS = 5


def pending_notification_ids() -> list[int]:
    """The ids of the notifications still to be e-mailed, oldest first."""
    return list(
        Notification.objects.filter(
            email_sent_at__isnull=True, email_attempts__lt=MAX_EMAIL_ATTEMPTS
        )
        .order_by("id")
        .values_list("id", flat=True)
    )


def email_for(notification: Notification) -> tuple[str, str]:
    subject = NotificationType(notification.type).label
    text = message_for(notification.type, notification.payload)
    return subject, f"{text}\n\nAcesse o SIGTCC: {settings.FRONTEND_URL}"


def send_notification_email(notification: Notification) -> None:
    subject, body = email_for(notification)
    send_mail(subject, body, None, [notification.recipient.email])
