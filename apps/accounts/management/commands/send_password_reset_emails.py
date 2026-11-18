import logging

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.password_reset import (
    delete_old_settled_requests,
    discard_stale_requests,
    lock_pending_request,
    pending_request_ids,
    record_failed_attempt,
    settle_request,
)

logger = logging.getLogger("sigtcc.auth")


class Command(BaseCommand):
    help = (
        "E-mails the link of each pending password reset request, and settles without a mail the "
        "ones with no active account (RF07, ADR 0020). Safe to run again: a request already "
        "settled is not sent twice. A failure leaves the request pending, up to 5 attempts, and "
        "does not stop the others."
    )

    def handle(self, *args, **options):
        discard_stale_requests()
        delete_old_settled_requests()
        sent = 0
        failed: list[int] = []
        for request_id in pending_request_ids():
            try:
                with transaction.atomic():
                    request = lock_pending_request(request_id)
                    if request is None:
                        # Another run is settling it, or it was settled meanwhile.
                        continue
                    sent += settle_request(request)
            except Exception:
                # The transaction above is undone, so the request stays pending.
                logger.exception("failed to e-mail password reset request %s", request_id)
                failed.append(request_id)
                try:
                    record_failed_attempt(request_id)
                except Exception:
                    # Counting must never stop the others.
                    logger.exception("failed to count the attempt of request %s", request_id)
        self.stdout.write(f"{sent} e-mail(s) enviado(s)." if sent else "Nenhum e-mail enviado.")
        if failed:
            raise CommandError(
                f"{len(failed)} pedido(s) de redefinição não puderam ser enviados "
                f"(ids {', '.join(map(str, failed))}); a próxima execução tenta de novo."
            )
