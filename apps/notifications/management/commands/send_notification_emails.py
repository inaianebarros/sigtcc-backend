import logging

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.notifications.emails import (
    lock_pending_notification,
    pending_notification_ids,
    record_failed_attempt,
    send_notification_email,
)

logger = logging.getLogger("sigtcc.notifications")


class Command(BaseCommand):
    help = (
        "E-mails each pending notification to its recipient (RF54, RNF04, ADR 0011). Safe to run "
        "again: a notification already e-mailed is not sent twice. A failure leaves the "
        "notification pending, up to 5 attempts, and does not stop the others."
    )

    def handle(self, *args, **options):
        sent = 0
        failed: list[int] = []
        for notification_id in pending_notification_ids():
            mailed = False
            try:
                with transaction.atomic():
                    notification = lock_pending_notification(notification_id)
                    if notification is None:
                        # Another run is sending it, or it was sent meanwhile.
                        continue
                    # A deactivated account (RN19) is settled without a mail, and it is no failure.
                    if notification.recipient.is_active:
                        send_notification_email(notification)
                        mailed = True
                    notification.email_sent_at = timezone.now()
                    notification.save(update_fields=["email_sent_at"])
            except Exception:
                # The transaction above is undone; the attempt is counted outside of it.
                logger.exception("failed to e-mail notification %s", notification_id)
                failed.append(notification_id)
                try:
                    record_failed_attempt(notification_id)
                except Exception:
                    # Counting must never stop the others.
                    logger.exception(
                        "failed to count the attempt of notification %s", notification_id
                    )
            else:
                sent += mailed
        self.stdout.write(f"{sent} e-mail(s) enviado(s)." if sent else "Nenhum e-mail enviado.")
        if failed:
            raise CommandError(
                f"{len(failed)} notificação(ões) não puderam ser enviadas "
                f"(ids {', '.join(map(str, failed))}); a próxima execução tenta de novo."
            )
