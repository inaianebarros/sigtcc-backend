from django.db.models import QuerySet

from apps.accounts.models import User

from .models import Notification


def list_notifications(user: User, is_read: bool | None) -> QuerySet[Notification]:
    """The user's own notifications, newest first (ties by id), optionally only read or unread."""
    notifications = Notification.objects.filter(recipient=user)
    if is_read is not None:
        notifications = notifications.filter(is_read=is_read)
    return notifications.order_by("-created_at", "-id")


def unread_count(user: User) -> int:
    return Notification.objects.filter(recipient=user, is_read=False).count()
