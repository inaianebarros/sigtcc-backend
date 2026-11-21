import logging

from django.core.management.base import BaseCommand

from apps.advising.models import ReportExport, ReportExportStatus
from apps.advising.report_export import build_export, claim_export, queued_export_ids

logger = logging.getLogger("sigtcc.exports")


class Command(BaseCommand):
    help = (
        "Builds the PDF of each queued export of the report of an ended advising (RF62, RN50, "
        "ADR 0027), in order of request. Safe to run again: an export taken by another run is "
        "skipped. A failure marks that export as failed and does not stop the others."
    )

    def handle(self, *args, **options):
        built = 0
        for export_id in queued_export_ids():
            export = claim_export(export_id)
            if export is None:
                continue
            try:
                build_export(export)
                built += 1
            except Exception:
                logger.exception("failed to build report export %s", export_id)
                ReportExport.objects.filter(pk=export_id).update(status=ReportExportStatus.FAILED)
        self.stdout.write(f"{built} PDF(s) gerado(s)." if built else "Nenhum PDF gerado.")
