import datetime
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from apps.accounts.models import UserType
from apps.advising.models import Advising, AdvisingStatus
from apps.meetings.models import Meeting, MeetingStatus
from apps.notifications.models import Notification
from tests.api.helpers import bearer, post_json
from tests.factories import (
    make_advising,
    make_course,
    make_meeting,
    make_student,
    make_teacher,
    make_user,
)

pytestmark = pytest.mark.django_db

SANTAREM = ZoneInfo("America/Santarem")


def url(meeting):
    return f"/api/reunioes/{meeting.id}/apontamentos"


def write(client, user, meeting, notes):
    return client.put(
        url(meeting),
        {"notes": notes},
        content_type="application/json",
        headers=bearer(user),
    )


def in_days(days, hour=10):
    day = (timezone.now().astimezone(SANTAREM) + datetime.timedelta(days=days)).date()
    return datetime.datetime(day.year, day.month, day.day, hour, 0, tzinfo=SANTAREM)


def advised_pair():
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    return student, teacher, make_advising(student=student, teacher=teacher)


def accepted(advising, days=3):
    return make_meeting(advising, MeetingStatus.ACCEPTED, scheduled_at=in_days(days))


def marked(advising, client, teacher, days=3):
    meeting = accepted(advising, days)
    post_json(client, f"/api/reunioes/{meeting.id}/realizada", {}, headers=bearer(teacher.user))
    return meeting


def end(advising):
    Advising.objects.filter(pk=advising.pk).update(
        status=AdvisingStatus.ENDED, ended_at=timezone.now(), end_reason="Encerrada no teste."
    )


def test_the_advisor_writes_the_notes_of_a_meeting_marked_as_held_and_edits_them_later(client):
    _, teacher, advising = advised_pair()
    meeting = marked(advising, client, teacher)

    first = write(client, teacher.user, meeting, "Definimos o escopo do capítulo 2.")
    second = write(client, teacher.user, meeting, "Escopo do capítulo 2 revisto.")

    assert first.status_code == 200
    assert first.json()["notes"] == "Definimos o escopo do capítulo 2."
    assert second.json()["notes"] == "Escopo do capítulo 2 revisto."
    assert Meeting.objects.get(pk=meeting.pk).notes == "Escopo do capítulo 2 revisto."


def test_the_advisor_writes_the_notes_of_a_meeting_held_by_time(client):
    _, teacher, advising = advised_pair()
    meeting = accepted(advising, days=-1)

    response = write(client, teacher.user, meeting, "Revisamos a metodologia.")

    assert response.status_code == 200
    assert Meeting.objects.get(pk=meeting.pk).notes == "Revisamos a metodologia."
    assert Meeting.objects.get(pk=meeting.pk).held_at is None


def test_the_notes_are_optional_and_clearing_the_text_removes_them(client):
    _, teacher, advising = advised_pair()
    meeting = marked(advising, client, teacher)
    write(client, teacher.user, meeting, "Algo combinado.")

    cleared = write(client, teacher.user, meeting, "")
    spaces_only = write(client, teacher.user, meeting, "   \n  ")

    assert cleared.status_code == 200 and cleared.json()["notes"] == ""
    assert spaces_only.status_code == 200 and spaces_only.json()["notes"] == ""
    assert Meeting.objects.get(pk=meeting.pk).notes == ""


def test_the_text_is_trimmed_and_the_limit_is_3000_characters_without_the_ends(client):
    _, teacher, advising = advised_pair()
    meeting = marked(advising, client, teacher)

    exact = write(client, teacher.user, meeting, "  " + "a" * 3000 + "  \n")
    too_long = write(client, teacher.user, meeting, "a" * 3001)

    assert exact.status_code == 200
    assert exact.json()["notes"] == "a" * 3000
    assert too_long.status_code == 422
    assert too_long.json()["detail"][0]["loc"][-1] == "notes"
    assert Meeting.objects.get(pk=meeting.pk).notes == "a" * 3000


def test_the_notes_field_is_required(client):
    _, teacher, advising = advised_pair()
    meeting = marked(advising, client, teacher)

    response = client.put(
        url(meeting), {}, content_type="application/json", headers=bearer(teacher.user)
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "build",
    [
        lambda advising: make_meeting(advising, MeetingStatus.PENDING, scheduled_at=in_days(3)),
        lambda advising: make_meeting(advising, MeetingStatus.REJECTED, scheduled_at=in_days(3)),
        lambda advising: make_meeting(advising, MeetingStatus.CANCELLED, scheduled_at=in_days(3)),
        lambda advising: make_meeting(advising, MeetingStatus.ACCEPTED, scheduled_at=in_days(3)),
        lambda advising: make_meeting(advising, MeetingStatus.PENDING, scheduled_at=in_days(-2)),
    ],
    ids=["pending", "rejected", "cancelled", "accepted-future-unmarked", "expired"],
)
def test_a_meeting_that_is_not_held_takes_no_notes(client, build):
    _, teacher, advising = advised_pair()
    meeting = build(advising)
    if meeting.status == MeetingStatus.CANCELLED:
        Meeting.objects.filter(pk=meeting.pk).update(cancellation_reason="Imprevisto.")

    response = write(client, teacher.user, meeting, "Texto")

    assert response.status_code == 409
    assert response.json() == {"detail": "Só uma reunião realizada pode ter apontamentos."}
    assert Meeting.objects.get(pk=meeting.pk).notes == ""


def test_the_student_cannot_write_but_reads_the_notes_in_the_listing(client):
    student, teacher, advising = advised_pair()
    meeting = marked(advising, client, teacher)
    write(client, teacher.user, meeting, "Combinamos a entrega.")

    refused = write(client, student.user, meeting, "Tentativa do aluno")
    listing = client.get("/api/reunioes", headers=bearer(student.user)).json()["items"][0]

    assert refused.status_code == 403
    assert Meeting.objects.get(pk=meeting.pk).notes == "Combinamos a entrega."
    assert listing["notes"] == "Combinamos a entrega."
    assert listing["can_edit_notes"] is False


def test_admins_and_visitors_cannot_write(client):
    _, teacher, advising = advised_pair()
    meeting = marked(advising, client, teacher)

    assert write(client, make_user(UserType.ADMIN), meeting, "x").status_code == 403
    assert (
        client.put(url(meeting), {"notes": "x"}, content_type="application/json").status_code == 401
    )


def test_another_teacher_and_an_unknown_meeting_get_the_same_404(client):
    _, teacher, advising = advised_pair()
    meeting = marked(advising, client, teacher)
    other = make_teacher()

    refused = write(client, other.user, meeting, "Intruso")
    unknown = client.put(
        "/api/reunioes/999999/apontamentos",
        {"notes": "x"},
        content_type="application/json",
        headers=bearer(other.user),
    )

    assert refused.status_code == 404
    assert refused.json() == unknown.json() == {"detail": "Reunião não encontrada."}
    assert Meeting.objects.get(pk=meeting.pk).notes == ""


def test_with_the_advising_ended_the_notes_are_frozen_but_still_readable(client):
    student, teacher, advising = advised_pair()
    meeting = marked(advising, client, teacher)
    write(client, teacher.user, meeting, "Registro final.")
    end(advising)

    refused = write(client, teacher.user, meeting, "Alterado depois")
    teacher_view = client.get("/api/reunioes", headers=bearer(teacher.user)).json()["items"][0]
    student_view = client.get("/api/reunioes", headers=bearer(student.user)).json()["items"][0]

    assert refused.status_code == 404
    assert Meeting.objects.get(pk=meeting.pk).notes == "Registro final."
    assert teacher_view["notes"] == student_view["notes"] == "Registro final."
    assert teacher_view["can_edit_notes"] is False


def test_the_listing_tells_who_can_edit_the_notes(client):
    student, teacher, advising = advised_pair()
    held = marked(advising, client, teacher, days=3)
    by_time = accepted(advising, days=-2)
    future = accepted(advising, days=5)
    pending = make_meeting(advising, MeetingStatus.PENDING, scheduled_at=in_days(6))

    def flags(user):
        body = client.get("/api/reunioes", {"limit": 100}, headers=bearer(user)).json()
        return {item["id"]: item["can_edit_notes"] for item in body["items"]}

    teacher_view = flags(teacher.user)

    assert teacher_view[held.id] is True
    assert teacher_view[by_time.id] is True
    assert teacher_view[future.id] is False
    assert teacher_view[pending.id] is False
    assert not any(flags(student.user).values())


def test_the_calendar_reading_carries_the_notes_and_the_capability(client):
    student, teacher, advising = advised_pair()
    meeting = marked(advising, client, teacher)
    write(client, teacher.user, meeting, "Pauta combinada.")
    day = in_days(3).date().isoformat()

    (as_teacher,) = client.get(
        "/api/reunioes/calendario",
        {"date_from": day, "date_to": day},
        headers=bearer(teacher.user),
    ).json()
    (as_student,) = client.get(
        "/api/reunioes/calendario",
        {"date_from": day, "date_to": day},
        headers=bearer(student.user),
    ).json()

    assert (as_teacher["notes"], as_teacher["can_edit_notes"]) == ("Pauta combinada.", True)
    assert (as_student["notes"], as_student["can_edit_notes"]) == ("Pauta combinada.", False)


def test_writing_removing_or_editing_notes_sends_no_notification(client):
    _, teacher, advising = advised_pair()
    meeting = marked(advising, client, teacher)
    before = Notification.objects.count()

    write(client, teacher.user, meeting, "Primeiro texto.")
    write(client, teacher.user, meeting, "Texto revisto.")
    write(client, teacher.user, meeting, "")

    assert Notification.objects.count() == before


def test_the_openapi_documents_the_notes(client):
    schema = client.get("/api/openapi.json").json()

    operation = schema["paths"]["/api/reunioes/{meeting_id}/apontamentos"]["put"]
    assert {"200", "401", "403", "404", "409", "422"} <= set(operation["responses"])
    assert "RN44" in operation["description"]
    item = schema["components"]["schemas"]["MeetingItemOut"]["properties"]
    assert {"notes", "can_edit_notes"} <= set(item)
    notes_in = schema["components"]["schemas"]["MeetingNotesIn"]["properties"]["notes"]
    assert notes_in["maxLength"] == 3000
