"""The e-mail of a notification (RF54): the label of the type as subject, the same text the system
shows as body, and a link to the system. Plain text, and nothing the notification does not show."""

import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import F, Q
from django.utils import timezone

from apps.accounts.models import UserType

from .messages import message_date, message_for
from .models import Notification, NotificationType

MAX_EMAIL_ATTEMPTS = 5

#: Notices the person may have seen on the screen that caused them (RN50): read there, they get no
#: e-mail. Each waits a little before going out, so the screen has the time to mark it read.
SKIPPED_WHEN_READ = (NotificationType.REPORT_EXPORT_READY,)
SEEN_ON_SCREEN_GRACE = timedelta(seconds=15)

logger = logging.getLogger("sigtcc.notifications")


def pending_notification_ids() -> list[int]:
    """The ids of the notifications still to be e-mailed, oldest first."""
    too_recent = Q(type__in=SKIPPED_WHEN_READ, created_at__gt=timezone.now() - SEEN_ON_SCREEN_GRACE)
    return list(
        Notification.objects.filter(
            email_sent_at__isnull=True, email_attempts__lt=MAX_EMAIL_ATTEMPTS
        )
        .exclude(too_recent)
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


def seen_on_screen(notification: Notification) -> bool:
    """A notice of the screen the person was on, already read there: it needs no e-mail."""
    return notification.type in SKIPPED_WHEN_READ and notification.is_read


def _report_screen(notification: Notification) -> str:
    area = "aluno" if notification.recipient.type == UserType.STUDENT else "professor"
    advising_id = notification.payload["advising_id"]
    return f"{settings.FRONTEND_URL}/{area}/historico/{advising_id}/relatorio"


def _report_export_ready_body(notification: Notification) -> str:
    payload = notification.payload
    ended = message_date(payload["ended_on"])
    until = message_date(payload["available_until"])
    return (
        f"O PDF do relatório da sua orientação com {payload['counterpart_name']}, encerrada em "
        f"{ended}, está pronto. Ele fica disponível até {until}, e só você pode baixá-lo.\n\n"
        "O PDF não vai anexado a este e-mail. Para baixá-lo, abra o relatório no SIGTCC (será "
        f"preciso entrar com a sua conta):\n{_report_screen(notification)}"
    )


def email_for(notification: Notification) -> tuple[str, str]:
    subject = NotificationType(notification.type).label
    if notification.type == NotificationType.REPORT_EXPORT_READY:
        return subject, _report_export_ready_body(notification)
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
