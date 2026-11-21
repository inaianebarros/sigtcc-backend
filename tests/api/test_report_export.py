"""The export of the report of an ended advising to PDF (RF62, RN50, ADR 0027)."""

import pytest
from django.core.management import CommandError
from django.db.models.signals import post_save

from apps.activities.models import ActivityStatus
from apps.advising.models import ReportExport
from tests.api.report_builders import REASON, add_activity, add_meeting, ended_advising
from tests.api.report_export_flow import (
    SANTAREM,
    ask_export,
    current_export,
    download,
    pdf_text,
    run_worker,
)
from tests.factories import make_admin, make_advising, make_student, make_teacher

pytestmark = pytest.mark.django_db


def test_a_student_asks_the_export_and_downloads_the_pdf_with_the_header_and_the_totals(client):
    teacher = make_teacher(full_name="Paulo Professor")
    student = make_student(full_name="Ana Aluna")
    advising = ended_advising(student, teacher, topic="Aprendizado de máquina")
    add_meeting(advising)
    add_activity(advising)
    add_activity(advising, status=ActivityStatus.ARCHIVED)

    asked = ask_export(client, student.user, advising.id)
    run_worker()
    response = download(client, student.user, advising.id)

    assert asked.status_code == 202
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    text = pdf_text(response)
    started = advising.started_at.astimezone(SANTAREM).strftime("%d/%m/%Y")
    ended = advising.ended_at.astimezone(SANTAREM).strftime("%d/%m/%Y")
    for expected in (
        "Relatório da orientação",
        "Professor orientador",
        "Paulo Professor",
        "Encerrada",
        f"Iniciada em {started}",
        f"Encerrada em {ended}",
        REASON,
        "Aprendizado de máquina",
        "Reuniões realizadas: 1",
        "Atividades concluídas ou arquivadas: 2",
    ):
        assert expected in text


def test_a_teacher_downloads_the_pdf_with_the_student_as_the_other_party_and_a_named_file(client):
    teacher = make_teacher()
    student = make_student(full_name="Ana Clara Sousa")
    advising = ended_advising(student, teacher)

    ask_export(client, teacher.user, advising.id)
    run_worker()
    response = download(client, teacher.user, advising.id)

    ended = advising.ended_at.astimezone(SANTAREM).date().isoformat()
    assert response["Content-Disposition"] == (
        f'attachment; filename="relatorio-orientacao-ana-clara-sousa-{ended}.pdf"'
    )
    text = pdf_text(response)
    assert "Aluno" in text and "Ana Clara Sousa" in text
    assert "Sem tema cadastrado" in text


def test_the_current_export_goes_from_none_to_queued_to_ready(client):
    student = make_student()
    advising = ended_advising(student)

    before = current_export(client, student.user, advising.id)
    asked = ask_export(client, student.user, advising.id).json()
    queued = current_export(client, student.user, advising.id).json()
    early = download(client, student.user, advising.id)
    run_worker()
    ready = current_export(client, student.user, advising.id).json()

    assert before.status_code == 200 and before.json() is None
    assert asked["status"] == "queued" and asked["ready_at"] is None
    assert queued["id"] == asked["id"] and queued["status"] == "queued"
    assert early.status_code == 409
    assert ready["status"] == "ready" and ready["ready_at"]
    assert asked["file_name"] is None
    assert ready["file_name"].startswith("relatorio-orientacao-")


def test_the_other_party_does_not_download_the_pdf_the_requester_asked_for(client):
    teacher = make_teacher()
    student = make_student()
    advising = ended_advising(student, teacher)
    ask_export(client, student.user, advising.id)
    run_worker()

    assert current_export(client, teacher.user, advising.id).json() is None
    assert download(client, teacher.user, advising.id).status_code == 409


@pytest.mark.parametrize("endpoint", [ask_export, current_export, download])
def test_anyone_else_an_unknown_id_and_an_active_advising_get_the_404_of_the_report(
    client, endpoint
):
    student = make_student()
    advising = ended_advising(student)
    active = make_advising(student=make_student())

    assert endpoint(client, make_student().user, advising.id).status_code == 404
    assert endpoint(client, make_teacher().user, advising.id).status_code == 404
    assert endpoint(client, student.user, 999_999).status_code == 404
    assert endpoint(client, active.student.user, active.id).status_code == 404
    assert endpoint(client, active.teacher.user, active.id).status_code == 404
    assert endpoint(client, make_admin(), advising.id).status_code == 403


def test_an_export_that_cannot_be_kept_in_the_bucket_fails_and_a_new_request_is_built(
    client, settings
):
    student = make_student()
    advising = ended_advising(student)
    ask_export(client, student.user, advising.id)
    storages = settings.STORAGES
    settings.STORAGES = {
        **storages,
        "submissions": {"BACKEND": "tests.api.failing_storage.FailingStorage"},
    }
    with pytest.raises(CommandError):
        run_worker()
    settings.STORAGES = storages

    assert current_export(client, student.user, advising.id).json()["status"] == "failed"
    assert download(client, student.user, advising.id).status_code == 409
    ask_export(client, student.user, advising.id)
    run_worker()
    assert download(client, student.user, advising.id).status_code == 200


def test_the_current_export_shows_the_stage_and_the_count_while_the_pdf_is_built(client):
    student = make_student()
    advising = ended_advising(student)
    add_meeting(advising)
    add_activity(advising)
    add_activity(advising, status=ActivityStatus.ARCHIVED)
    ask_export(client, student.user, advising.id)
    seen = []

    def look(sender, instance, **kwargs):
        body = current_export(client, student.user, advising.id).json()
        seen.append((body["status"], body["stage"], body["items_done"], body["items_total"]))

    post_save.connect(look, sender=ReportExport)
    try:
        queued = current_export(client, student.user, advising.id).json()
        run_worker()
    finally:
        post_save.disconnect(look, sender=ReportExport)

    assert (queued["stage"], queued["items_done"], queued["items_total"]) == ("queued", 0, 0)
    gathering = [step for step in seen if step[1] == "gathering"]
    assert gathering[0] == ("processing", "gathering", 0, 3)
    assert gathering[-1] == ("processing", "gathering", 3, 3)
    assert [done for _, _, done, _ in gathering] == sorted(done for _, _, done, _ in gathering)
    assert ("processing", "rendering", 3, 3) in seen
    assert seen[-1][0] == "ready"
