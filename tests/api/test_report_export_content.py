"""What the PDF of the export of the report holds (RF62, RN50): every record of the ended
advising, grouped in meetings and activities, with each activity's timeline."""

import re
from datetime import date, datetime

import pytest

from apps.activities.models import Activity
from apps.meetings.models import MeetingStatus
from tests.api.activity_flow import (
    accept,
    advised_pair,
    change_status,
    comment,
    create_activity,
    deliver,
    end_advising,
    refuse,
)
from tests.api.helpers import bearer
from tests.api.report_builders import add_meeting, ended_advising
from tests.api.report_export_flow import (
    SANTAREM,
    ask_export,
    current_export,
    download,
    exported_text,
    pdf_reader,
    run_worker,
)
from tests.api.uploads import PDF, upload
from tests.factories import make_student

pytestmark = pytest.mark.django_db


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=SANTAREM)


def test_the_meetings_section_lists_every_held_meeting_with_its_times_and_notes(client):
    student = make_student()
    advising = ended_advising(student)
    add_meeting(
        advising,
        at=at(date(2026, 9, 1), 14),
        description="Definir o tema",
        notes="Ler dois artigos sobre o tema.",
    )
    add_meeting(advising, at=at(date(2026, 10, 6), 9, 30), description="Revisar a introdução")
    add_meeting(advising, status=MeetingStatus.CANCELLED, description="Reunião cancelada")
    add_meeting(advising, status=MeetingStatus.PENDING, description="Reunião pendente")
    add_meeting(advising, status=MeetingStatus.REJECTED, description="Reunião recusada")
    # 22:00 in Santarém is already the next day in UTC: the day shown is the local one.
    add_meeting(advising, at=at(date(2026, 8, 3), 22), description="Reunião à noite")

    text = exported_text(client, student.user, advising.id)

    meetings = text[text.rindex("Reuniões realizadas") :]
    assert "06/10/2026, das 09:30 às 10:30" in meetings
    assert "01/09/2026, das 14:00 às 15:00" in meetings
    assert meetings.index("Revisar a introdução") < meetings.index("Definir o tema")
    assert "Realizada" in meetings
    assert "Ler dois artigos sobre o tema." in meetings
    assert "Sem apontamentos registrados." in meetings
    assert "Reunião cancelada" not in text
    assert "Reunião pendente" not in text
    assert "Reunião recusada" not in text
    assert "03/08/2026, das 22:00 às 23:00" in meetings
    assert meetings.index("Definir o tema") < meetings.index("Reunião à noite")


def test_the_activities_section_lists_each_activity_with_its_whole_timeline(client):
    student, teacher, advising = advised_pair()
    reviewed = create_activity(client, teacher, student)
    deliver(client, student, reviewed)
    change_status(client, student, reviewed, "in_review")
    refuse(client, teacher, reviewed, files=[upload("correcoes.pdf", PDF)])
    deliver(client, student, reviewed)
    change_status(client, student, reviewed, "in_review")
    comment(client, teacher.user, reviewed, text="Ficou muito melhor.")
    accept(client, teacher, reviewed)
    left_pending = create_activity(client, teacher, student)
    deleted = create_activity(client, teacher, student)
    client.delete(f"/api/atividades/{deleted.id}", headers=bearer(teacher.user))
    Activity.objects.filter(pk=reviewed.pk).update(title="Capítulo 1", deadline=date(2026, 9, 10))
    Activity.objects.filter(pk=left_pending.pk).update(
        title="Capítulo 3", deadline=date(2026, 11, 1)
    )
    Activity.objects.filter(pk=deleted.pk).update(title="Capítulo apagado")
    end_advising(client, teacher, advising)

    text = exported_text(client, student.user, advising.id)

    activities = text[text.rindex("Atividades concluídas ou arquivadas") :]
    assert activities.index("Capítulo 3") < activities.index("Capítulo 1")
    assert "Prazo: 01/11/2026" in activities and "Prazo: 10/09/2026" in activities
    assert "Arquivada ao encerrar a orientação" in activities
    assert "Concluída" in activities
    assert "Entregar o capítulo 2" in activities
    for event in (
        "Atividade criada",
        "1ª entrega",
        "2ª entrega",
        "Enviada para revisão",
        "Revisão da 1ª entrega",
        "Atividade recusada",
        "Refazer.",
        "Revisão da 2ª entrega",
        "Atividade aceita",
        "Comentário",
        "Ficou muito melhor.",
        "a.pdf",
        "correcoes.pdf",
    ):
        assert event in activities
    assert activities.index("Atividade aceita") < activities.index("Atividade recusada")
    assert "Capítulo apagado" not in text


def test_a_section_without_records_says_so(client):
    student = make_student()
    advising = ended_advising(student)

    text = exported_text(client, student.user, advising.id)

    assert "Nenhuma reunião realizada." in text
    assert "Nenhuma atividade concluída ou arquivada." in text


def test_every_page_has_the_footer_with_when_it_was_built_and_the_page_number(client):
    student = make_student()
    advising = ended_advising(student)
    for number in range(40):
        add_meeting(advising, description=f"Reunião {number}", notes="Apontamento longo. " * 20)
    ask_export(client, student.user, advising.id)
    run_worker()

    reader = pdf_reader(download(client, student.user, advising.id))

    built = current_export(client, student.user, advising.id).json()["ready_at"]
    day = datetime.fromisoformat(built).astimezone(SANTAREM).strftime("%d/%m/%Y")
    pages = [page.extract_text() for page in reader.pages]
    assert len(pages) > 1
    for number, page in enumerate(pages, start=1):
        assert re.search(rf"Gerado pelo SIGTCC em {re.escape(day)} às \d\d:\d\d", page)
        assert f"Página {number} de {len(pages)}" in page


def test_the_pdf_is_tagged_for_accessibility(client):
    student = make_student()
    advising = ended_advising(student)
    ask_export(client, student.user, advising.id)
    run_worker()

    reader = pdf_reader(download(client, student.user, advising.id))

    metadata = reader.trailer["/Root"]["/Metadata"].get_object().get_data().decode()
    assert "pdfuaid" in metadata
    assert bool(reader.trailer["/Root"]["/MarkInfo"]["/Marked"])
    assert reader.trailer["/Root"]["/Lang"] == "pt-BR"
    page = reader.pages[0].mediabox
    assert (round(float(page.width)), round(float(page.height))) == (595, 842)  # A4 in points
