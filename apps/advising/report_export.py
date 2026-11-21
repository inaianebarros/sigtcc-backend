"""The Exportação do Relatório (RF62, RN50, ADR 0027): a party of an ended advising asks for the
report in PDF; the `send_report_exports` command builds it outside the request and keeps it in the
bucket, from where only the requester downloads it."""

import logging
import uuid
from datetime import timedelta

from django.core.files.base import ContentFile
from django.core.files.storage import Storage, storages
from django.db import transaction
from django.db.models import Q
from django.db.models.functions import TruncDate
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.text import slugify
from ninja.errors import HttpError

from apps.accounts.models import User, UserType
from apps.activities.timeline import whole_timeline
from apps.notifications.models import (
    ADVISING_REPORT_REFERENCE,
    Notification,
    NotificationType,
)
from apps.notifications.services import notify

from .models import (
    REPORT_EXPORT_VALID_FOR,
    Advising,
    ReportExport,
    ReportExportStage,
    ReportExportStatus,
)
from .report import advising_report, read_ended_advising, report_activities, report_meetings
from .thesis import topic_of

logger = logging.getLogger("sigtcc.exports")

PDF_NOT_READY_MESSAGE = "O PDF ainda não está pronto."
PDF_EXPIRED_MESSAGE = "Este PDF expirou. Gere um novo pelo botão Exportar PDF."
#: The wait before the next attempt doubles from this one: 30 s, 1, 2 and 4 min, so a
#: bucket outage of a few minutes does not use up the attempts.
FIRST_RETRY_AFTER = timedelta(seconds=30)


def export_storage() -> Storage:
    """The same bucket of the delivered files (ADR 0002), under its own prefix."""
    return storages["submissions"]


def request_export(user: User, advising_id: int) -> ReportExport:
    """Queues an export of the report for one of its parties, or returns the valid one the person
    already has (RN50); anyone else gets the 404 of the report (RN47)."""
    advising = read_ended_advising(user, advising_id)
    with transaction.atomic():
        # Two quick requests wait for each other here, so the second one finds the first.
        Advising.objects.select_for_update().filter(pk=advising.pk).first()
        latest = _latest_of(user, advising)
        if latest is not None and latest.is_valid(timezone.now()):
            return latest
        return ReportExport.objects.create(advising=advising, requester=user)


def current_export(user: User, advising_id: int) -> ReportExport | None:
    """The latest export the user asked for that advising, or none."""
    return _latest_of(user, read_ended_advising(user, advising_id))


def _latest_of(user: User, advising: Advising) -> ReportExport | None:
    return (
        ReportExport.objects.filter(advising=advising, requester=user)
        .order_by("-created_at", "-id")
        .first()
    )


def ready_export(user: User, advising_id: int) -> ReportExport:
    """The latest export of the user when its PDF is ready; only the requester downloads it."""
    export = current_export(user, advising_id)
    if export is not None and export.is_expired(timezone.now()):
        raise HttpError(410, PDF_EXPIRED_MESSAGE)
    if export is None or export.status != ReportExportStatus.READY:
        raise HttpError(409, PDF_NOT_READY_MESSAGE)
    return export


def remove_expired_pdfs() -> int:
    """Removes from the bucket the PDFs past their 7 days and marks them expired (RN50): the
    Railway bucket has no lifecycle rule, so the worker does it once an hour (ADR 0027)."""
    expired = ReportExport.objects.filter(
        status=ReportExportStatus.READY,
        ready_at__lt=timezone.now() - REPORT_EXPORT_VALID_FOR,
    )
    storage = export_storage()
    removed = 0
    for export in expired:
        try:
            storage.delete(export.storage_key)
        except Exception:
            # One file the bucket refuses must not keep the others; the next run tries again.
            logger.exception("failed to remove the expired PDF of report export %s", export.id)
            continue
        export.status = ReportExportStatus.EXPIRED
        export.save(update_fields=["status"])
        removed += 1
    return removed


def queued_export_ids() -> list[int]:
    return list(
        ReportExport.objects.filter(status=ReportExportStatus.QUEUED)
        .filter(Q(retry_at__isnull=True) | Q(retry_at__lte=timezone.now()))
        .order_by("created_at", "id")
        .values_list("id", flat=True)
    )


#: How many times the worker tries to build a PDF before giving up on it (RN50).
MAX_EXPORT_ATTEMPTS = 5


def claim_export(export_id: int) -> ReportExport | None:
    """Moves a queued export to processing, unless another run took it first. The request of an
    account deactivated meanwhile (RN19) is dropped without a PDF or a notice."""
    with transaction.atomic():
        export = (
            ReportExport.objects.select_for_update(skip_locked=True)
            .filter(pk=export_id, status=ReportExportStatus.QUEUED)
            .select_related("requester", "advising__student__user")
            .first()
        )
        if export is None:
            return None
        if not export.requester.is_active:
            export.delete()
            return None
        export.status = ReportExportStatus.PROCESSING
        export.save(update_fields=["status"])
    return export


def build_export(export: ReportExport) -> None:
    """Gathers the records, counting them as it goes, then renders the PDF and keeps it; each step
    is saved, so the screen follows the stage and the count (RF62, ADR frontend 0007)."""
    from weasyprint import HTML  # heavy: loaded only by the worker

    report = advising_report(export.requester, export.advising_id)
    export.items_total = report.records_count
    _advance(export, ReportExportStage.GATHERING, 0)
    meetings = []
    for meeting in (
        report_meetings(report).annotate(day=TruncDate("scheduled_at")).order_by("-day", "-id")
    ):
        meetings.append(meeting)
        _advance(export, ReportExportStage.GATHERING, export.items_done + 1)
    activities = list(
        report_activities(report)
        .select_related("advising__student__user", "advising__teacher__user")
        .order_by("-deadline", "-id")
    )
    for activity in activities:
        activity.events = whole_timeline(activity)
        _advance(export, ReportExportStage.GATHERING, export.items_done + 1)

    _advance(export, ReportExportStage.RENDERING, export.items_done)
    built_at = timezone.now()
    html = render_to_string(
        "advising/report_export.html",
        {
            "report": report,
            "topic": topic_of(report),
            "built_at": built_at,
            "meetings": meetings,
            "activities": activities,
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
    try:
        with transaction.atomic():
            export.save(update_fields=["storage_key", "file_name", "status", "ready_at"])
            _notify_ready(export, report.counterpart_name)
    except Exception:
        # The export goes back to the queue: the file it will not point to must not stay behind.
        export_storage().delete(key)
        raise


def _notify_ready(export: ReportExport, counterpart_name: str) -> None:
    """Tells the requester the PDF is ready (RN50): the notice opens the report screen, where the
    download is, and the e-mail goes only if the person did not see it there (ADR 0027)."""
    _notify(
        export,
        NotificationType.REPORT_EXPORT_READY,
        counterpart_name,
        available_until=timezone.localdate(export.available_until).isoformat(),
    )


def _notify(
    export: ReportExport, notification_type: NotificationType, counterpart_name: str, **extra
) -> None:
    notify(
        export.requester,
        notification_type,
        reference_type=ADVISING_REPORT_REFERENCE,
        reference_id=export.advising_id,
        payload={
            "export_id": export.id,
            "advising_id": export.advising_id,
            "counterpart_name": counterpart_name,
            "ended_on": timezone.localdate(export.advising.ended_at).isoformat(),
            **extra,
        },
    )


#: The notices of the result of an export: the screen marks them read when it shows it (RN50).
RESULT_NOTICES = (NotificationType.REPORT_EXPORT_READY, NotificationType.REPORT_EXPORT_FAILED)


def notification_of(export: ReportExport) -> Notification | None:
    """The notice of the result of the export (ready or failed), or none while it runs."""
    return Notification.objects.filter(
        recipient_id=export.requester_id,
        type__in=RESULT_NOTICES,
        payload__export_id=export.id,
    ).first()


def _advance(export: ReportExport, stage: ReportExportStage, done: int) -> None:
    export.stage, export.items_done = stage, done
    export.save(update_fields=["stage", "items_done", "items_total"])


def export_file_name(export: ReportExport) -> str:
    """`relatorio-orientacao-{nome-do-aluno}-{data-de-encerramento}.pdf`, without accents."""
    student = slugify(export.advising.student.user.full_name)
    ended = timezone.localdate(export.advising.ended_at).isoformat()
    return f"relatorio-orientacao-{student}-{ended}.pdf"


def record_export_failure(export_id: int) -> bool:
    """Counts a failed attempt (RN50): before the fifth, the export goes back to the queue from the
    start, after a wait that doubles each time; at the fifth it fails and the requester is told, in
    the same transaction. Says whether it failed for good."""
    with transaction.atomic():
        export = (
            ReportExport.objects.select_for_update()
            .select_related("requester", "advising__student__user", "advising__teacher__user")
            .get(pk=export_id)
        )
        export.attempts += 1
        if export.attempts < MAX_EXPORT_ATTEMPTS:
            export.status = ReportExportStatus.QUEUED
            export.stage = ReportExportStage.QUEUED
            export.items_done = export.items_total = 0
            export.retry_at = timezone.now() + FIRST_RETRY_AFTER * 2 ** (export.attempts - 1)
            export.save(
                update_fields=[
                    "attempts",
                    "status",
                    "stage",
                    "items_done",
                    "items_total",
                    "retry_at",
                ]
            )
            return False
        export.status = ReportExportStatus.FAILED
        export.save(update_fields=["attempts", "status"])
        _notify(export, NotificationType.REPORT_EXPORT_FAILED, _counterpart_name(export))
    return True


def _counterpart_name(export: ReportExport) -> str:
    advising = export.advising
    student, teacher = advising.student.user, advising.teacher.user
    return teacher.full_name if export.requester_id == student.id else student.full_name
