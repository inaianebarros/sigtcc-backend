import logging

from django.core.management.base import BaseCommand, CommandError

from apps.advising.report_export import (
    build_export,
    claim_export,
    queued_export_ids,
    record_failed_attempt,
)

logger = logging.getLogger("sigtcc.exports")


class Command(BaseCommand):
    help = (
        "Builds the PDF of each queued export of the report of an ended advising (RF62, RN50, "
        "ADR 0027), in order of request. Safe to run again: an export taken by another run is "
        "skipped. A failure sends the export back to the queue, up to 5 attempts, and does "
        "not stop the others."
    )

    def handle(self, *args, **options):
        built = 0
        failed: list[int] = []
        for export_id in queued_export_ids():
            export = claim_export(export_id)
            if export is None:
                continue
            try:
                build_export(export)
                built += 1
            except Exception:
                logger.exception("failed to build report export %s", export_id)
                failed.append(export_id)
                try:
                    record_failed_attempt(export_id)
                except Exception:
                    # Marking must never stop the others.
                    logger.exception("failed to count the attempt of report export %s", export_id)
        self.stdout.write(f"{built} PDF(s) gerado(s)." if built else "Nenhum PDF gerado.")
        if failed:
            raise CommandError(
                f"{len(failed)} exportação(ões) do relatório falharam "
                f"(ids {', '.join(map(str, failed))})."
            )
