import logging

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.password_reset import lock_pending_request, pending_request_ids, settle_request

logger = logging.getLogger("sigtcc.auth")


class Command(BaseCommand):
    help = (
        "E-mails the link of each pending password reset request, and settles without a mail the "
        "ones with no active account (RF07, ADR 0020). Safe to run again: a request already "
        "settled is not sent twice. A failure leaves the request pending and does not stop the "
        "others."
    )

    def handle(self, *args, **options):
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
        self.stdout.write(f"{sent} e-mail(s) enviado(s)." if sent else "Nenhum e-mail enviado.")
        if failed:
            raise CommandError(
                f"{len(failed)} pedido(s) de redefinição não puderam ser enviados "
                f"(ids {', '.join(map(str, failed))}); a próxima execução tenta de novo."
            )
