import logging

from django.core.management.base import BaseCommand, CommandError

from apps.advising.report_export import (
    build_export,
    claim_export,
    mark_export_failed,
    queued_export_ids,
    remove_expired_pdfs,
)

logger = logging.getLogger("sigtcc.exports")


class Command(BaseCommand):
    help = (
        "Builds the PDF of each queued export of the report of an ended advising (RF62, RN50, "
        "ADR 0027), in order of request. Safe to run again: an export taken by another run is "
        "skipped. A failure marks that export as failed and does not stop the others."
    )

    def handle(self, *args, **options):
        remove_expired_pdfs()
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
                    mark_export_failed(export_id)
                except Exception:
                    # Marking must never stop the others.
                    logger.exception("failed to mark report export %s as failed", export_id)
        self.stdout.write(f"{built} PDF(s) gerado(s)." if built else "Nenhum PDF gerado.")
        if failed:
            raise CommandError(
                f"{len(failed)} exportação(ões) do relatório falharam "
                f"(ids {', '.join(map(str, failed))})."
            )
