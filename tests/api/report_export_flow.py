"""The export of the report through the API and the worker, shared by its tests (RF62, RN50)."""

import io
from zoneinfo import ZoneInfo

from django.core.management import call_command
from pypdf import PdfReader

from tests.api.helpers import bearer

SANTAREM = ZoneInfo("America/Santarem")


def export_url(advising_id):
    return f"/api/orientacao/historico/{advising_id}/exportacao"


def ask_export(client, user, advising_id):
    return client.post(export_url(advising_id), headers=bearer(user))


def current_export(client, user, advising_id):
    return client.get(export_url(advising_id), headers=bearer(user))


def download(client, user, advising_id):
    return client.get(f"{export_url(advising_id)}/pdf", headers=bearer(user))


def run_worker():
    call_command("send_report_exports", stdout=io.StringIO())


def pdf_reader(response) -> PdfReader:
    return PdfReader(io.BytesIO(b"".join(response.streaming_content)))


def pdf_text(response) -> str:
    return "\n".join(page.extract_text() for page in pdf_reader(response).pages)


def exported_text(client, user, advising_id) -> str:
    """Asks for the export, runs the worker and returns the text of the downloaded PDF."""
    ask_export(client, user, advising_id)
    run_worker()
    response = download(client, user, advising_id)
    assert response.status_code == 200
    return pdf_text(response)
