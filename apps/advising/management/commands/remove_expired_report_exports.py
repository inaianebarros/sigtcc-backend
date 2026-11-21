from django.core.management.base import BaseCommand

from apps.advising.report_export import remove_expired_pdfs


class Command(BaseCommand):
    help = (
        "Removes from the bucket the PDFs of the report exports past their 7 days and marks them "
        "expired (RN50, ADR 0027). The export loop runs it once an hour. Safe to run again."
    )

    def handle(self, *args, **options):
        removed = remove_expired_pdfs()
        self.stdout.write(f"{removed} PDF(s) vencido(s) removido(s).")
