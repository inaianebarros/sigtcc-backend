from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.notifications.emails import pending_notification_ids, send_notification_email
from apps.notifications.models import Notification


class Command(BaseCommand):
    help = (
        "E-mails each pending notification to its recipient (RF54, RNF04, ADR 0011). Safe to run "
        "again: a notification already e-mailed is not sent twice."
    )

    def handle(self, *args, **options):
        sent = 0
        for notification_id in pending_notification_ids():
            with transaction.atomic():
                notification = Notification.objects.select_related("recipient").get(
                    pk=notification_id
                )
                send_notification_email(notification)
                notification.email_sent_at = timezone.now()
                notification.save(update_fields=["email_sent_at"])
            sent += 1
        self.stdout.write(f"{sent} e-mail(s) enviado(s)." if sent else "Nenhum e-mail pendente.")
