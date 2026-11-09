from django.db import transaction
from ninja.errors import HttpError

from apps.accounts.models import User

from .models import Notification, NotificationType

NOTIFICATION_NOT_FOUND_MESSAGE = "Notificação não encontrada."


def notify(
    recipient: User,
    notification_type: NotificationType,
    *,
    reference_type: str,
    reference_id: int,
    payload: dict,
) -> Notification:
    """Creates a notification. The single entry point: business services call it inside their own
    transaction (ADR 0003), so a failed action leaves none and the polling delivers the rest.

    The payload is the snapshot of the event; the text is built from it when it is read.
    """
    return Notification.objects.create(
        recipient=recipient,
        type=notification_type,
        reference_type=reference_type,
        reference_id=reference_id,
        payload=payload,
    )


@transaction.atomic
def mark_as_read(user: User, notification_id: int) -> Notification:
    """Marks one of the user's own notifications as read; asking again changes nothing."""
    notification = Notification.objects.filter(pk=notification_id, recipient=user).first()
    if notification is None:
        raise HttpError(404, NOTIFICATION_NOT_FOUND_MESSAGE)
    if not notification.is_read:
        notification.is_read = True
        notification.save(update_fields=["is_read"])
    return notification


def mark_all_as_read(user: User) -> int:
    """Marks every unread notification of the user as read and says how many changed."""
    return Notification.objects.filter(recipient=user, is_read=False).update(is_read=True)
