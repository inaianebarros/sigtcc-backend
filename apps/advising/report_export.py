"""The Exportação do Relatório (RF62, RN50, ADR 0027): a party of an ended advising asks for the
report in PDF; the `send_report_exports` command builds it outside the request and keeps it in the
bucket, from where only the requester downloads it."""

import uuid

from django.core.files.base import ContentFile
from django.core.files.storage import Storage, storages
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.text import slugify
from ninja.errors import HttpError

from apps.accounts.models import User, UserType

from .models import ReportExport, ReportExportStatus
from .report import advising_report, read_ended_advising
from .thesis import topic_of

PDF_NOT_READY_MESSAGE = "O PDF ainda não está pronto."


def export_storage() -> Storage:
    """The same bucket of the delivered files (ADR 0002), under its own prefix."""
    return storages["submissions"]


def request_export(user: User, advising_id: int) -> ReportExport:
    """Queues an export of the report for one of its parties; anyone else gets the 404 of the
    report (RN47)."""
    advising = read_ended_advising(user, advising_id)
    return ReportExport.objects.create(advising=advising, requester=user)


def current_export(user: User, advising_id: int) -> ReportExport | None:
    """The latest export the user asked for that advising, or none."""
    advising = read_ended_advising(user, advising_id)
    return (
        ReportExport.objects.filter(advising=advising, requester=user)
        .order_by("-created_at", "-id")
        .first()
    )


def ready_export(user: User, advising_id: int) -> ReportExport:
    """The latest export of the user when its PDF is ready; only the requester downloads it."""
    export = current_export(user, advising_id)
    if export is None or export.status != ReportExportStatus.READY:
        raise HttpError(409, PDF_NOT_READY_MESSAGE)
    return export


def queued_export_ids() -> list[int]:
    return list(
        ReportExport.objects.filter(status=ReportExportStatus.QUEUED)
        .order_by("created_at", "id")
        .values_list("id", flat=True)
    )


def claim_export(export_id: int) -> ReportExport | None:
    """Moves a queued export to processing, unless another run took it first."""
    with transaction.atomic():
        export = (
            ReportExport.objects.select_for_update(skip_locked=True)
            .filter(pk=export_id, status=ReportExportStatus.QUEUED)
            .select_related("requester", "advising__student__user")
            .first()
        )
        if export is None:
            return None
        export.status = ReportExportStatus.PROCESSING
        export.save(update_fields=["status"])
    return export


def build_export(export: ReportExport) -> None:
    """Renders the PDF, keeps it in the bucket and marks the export ready."""
    from weasyprint import HTML  # heavy: loaded only by the worker

    report = advising_report(export.requester, export.advising_id)
    html = render_to_string(
        "advising/report_export.html",
        {
            "report": report,
            "topic": topic_of(report),
            "counterpart_label": (
                "Professor orientador" if export.requester.type == UserType.STUDENT else "Aluno"
            ),
        },
    )
    content = HTML(string=html).write_pdf(pdf_variant="pdf/ua-1")
    key = export_storage().save(
        f"exportacoes/{export.advising_id}/{uuid.uuid4().hex}.pdf", ContentFile(content)
    )
    export.storage_key = key
    export.file_name = export_file_name(export)
    export.status = ReportExportStatus.READY
    export.ready_at = timezone.now()
    export.save(update_fields=["storage_key", "file_name", "status", "ready_at"])


def export_file_name(export: ReportExport) -> str:
    """`relatorio-orientacao-{nome-do-aluno}-{data-de-encerramento}.pdf`, without accents."""
    student = slugify(export.advising.student.user.full_name)
    ended = timezone.localdate(export.advising.ended_at).isoformat()
    return f"relatorio-orientacao-{student}-{ended}.pdf"
