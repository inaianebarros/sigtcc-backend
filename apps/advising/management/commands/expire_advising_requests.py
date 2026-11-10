import logging

from django.core.management.base import BaseCommand, CommandError

from apps.advising.deadlines import answer_cutoff
from apps.advising.expiration import expire_request, overdue_request_ids

logger = logging.getLogger("sigtcc.advising")


class Command(BaseCommand):
    help = (
        "Marks as unanswered the pending advising requests with more than 7 days and notifies "
        "their students (RN20, RF44, RF45). Safe to run again: it only touches what is overdue."
    )

    def handle(self, *args, **options):
        cutoff = answer_cutoff()
        expired = 0
        failed: list[int] = []
        for request_id in overdue_request_ids(cutoff=cutoff):
            try:
                if expire_request(request_id, cutoff):
                    expired += 1
            except Exception:
                # Each request has its own transaction: this one is undone, the others go on.
                logger.exception("failed to expire advising request %s", request_id)
                failed.append(request_id)
        if expired:
            self.stdout.write(f"{expired} solicitação(ões) marcada(s) como não respondida(s).")
        elif not failed:
            self.stdout.write("Nenhuma solicitação vencida.")
        if failed:
            raise CommandError(
                f"{len(failed)} solicitação(ões) não puderam ser marcadas "
                f"(ids {', '.join(map(str, failed))}); a próxima execução tenta de novo."
            )
