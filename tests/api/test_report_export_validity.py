"""One valid export per person and advising, and the 7 days of the PDF (RN50)."""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from django.core.files.storage import storages
from django.utils import timezone

from tests.api.report_builders import ended_advising
from tests.api.report_export_flow import ask_export, current_export, download, run_worker
from tests.factories import make_student

pytestmark = pytest.mark.django_db

VALID_FOR = timedelta(days=7)


def later(by: timedelta):
    return patch("django.utils.timezone.now", return_value=timezone.now() + by)


def ready_export(client):
    student = make_student()
    advising = ended_advising(student)
    ask_export(client, student.user, advising.id)
    run_worker()
    return student, advising, current_export(client, student.user, advising.id).json()


def stored_pdfs(advising_id):
    return storages["submissions"].listdir(f"exportacoes/{advising_id}")[1]


def test_asking_again_while_the_export_is_running_returns_the_same_one(client):
    student = make_student()
    advising = ended_advising(student)

    first = ask_export(client, student.user, advising.id).json()
    again = ask_export(client, student.user, advising.id).json()

    assert again["id"] == first["id"] and again["status"] == "queued"


def test_asking_again_with_a_ready_pdf_returns_it_without_building_another(client):
    student, advising, ready = ready_export(client)

    again = ask_export(client, student.user, advising.id).json()
    run_worker()

    assert again["id"] == ready["id"] and again["status"] == "ready"
    assert len(stored_pdfs(advising.id)) == 1


def test_a_ready_pdf_says_until_when_it_can_be_downloaded(client):
    _, _, ready = ready_export(client)

    ready_at = datetime.fromisoformat(ready["ready_at"])
    assert datetime.fromisoformat(ready["available_until"]) == ready_at + VALID_FOR


def test_after_seven_days_the_pdf_is_expired_and_its_download_is_refused(client):
    student, advising, ready = ready_export(client)

    with later(VALID_FOR + timedelta(minutes=1)):
        expired = current_export(client, student.user, advising.id).json()
        refused = download(client, student.user, advising.id)

    assert expired["id"] == ready["id"] and expired["status"] == "expired"
    assert refused.status_code == 410
    assert refused.json()["detail"] == "Este PDF expirou. Gere um novo pelo botão Exportar PDF."


def test_right_before_seven_days_the_pdf_is_still_downloaded(client):
    student, advising, _ = ready_export(client)

    with later(VALID_FOR - timedelta(minutes=1)):
        response = download(client, student.user, advising.id)

    assert response.status_code == 200


def test_after_seven_days_asking_again_builds_a_new_pdf(client):
    student, advising, ready = ready_export(client)

    with later(VALID_FOR + timedelta(minutes=1)):
        again = ask_export(client, student.user, advising.id).json()
        run_worker()
        response = download(client, student.user, advising.id)

    assert again["id"] != ready["id"] and again["status"] == "queued"
    assert response.status_code == 200


def test_the_worker_removes_the_expired_pdfs_from_the_bucket_and_keeps_the_valid_ones(client):
    _, old, _ = ready_export(client)
    with later(VALID_FOR - timedelta(days=1)):
        _, recent, _ = ready_export(client)

    with later(VALID_FOR + timedelta(minutes=1)):
        run_worker()

    assert stored_pdfs(old.id) == []
    assert len(stored_pdfs(recent.id)) == 1
