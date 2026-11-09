import datetime
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import UserType
from apps.advising.models import Advising, AdvisingStatus
from apps.meetings.models import Meeting, MeetingStatus
from apps.meetings.services import FieldChange, edit_meeting
from tests.api.helpers import bearer, send_while_the_advising_ends
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


def url(meeting_id):
    return f"/api/reunioes/{meeting_id}"


def edit(client, user, meeting_id, body):
    return client.patch(
        url(meeting_id), data=body, content_type="application/json", headers=bearer(user)
    )


def advised_pair():
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    return student, teacher, make_advising(student=student, teacher=teacher)


def when(days=3, hour=10, minute=0):
    """A moment in Santarém `days` from now, at a round time, as the meeting stores it."""
    day = (timezone.now().astimezone(SANTAREM) + datetime.timedelta(days=days)).date()
    return datetime.datetime(day.year, day.month, day.day, hour, minute, tzinfo=SANTAREM)


def test_the_author_edits_the_description_and_the_response_has_the_creation_shape(client):
    student, _, advising = advised_pair()
    meeting = make_meeting(advising, author=student.user, scheduled_at=when())

    response = edit(client, student.user, meeting.id, {"description": "Nova pauta"})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"id", "scheduled_at", "description", "status", "author"}
    assert body["id"] == meeting.id
    assert body["description"] == "Nova pauta"
    assert body["status"] == "pending"
    assert body["scheduled_at"] == when().isoformat()
    assert body["author"]["id"] == student.user_id
    meeting.refresh_from_db()
    assert meeting.description == "Nova pauta"
    assert meeting.scheduled_at == when()


def date_of(moment):
    return moment.astimezone(SANTAREM).date().isoformat()


def time_of(moment):
    return moment.astimezone(SANTAREM).strftime("%H:%M")


def test_each_field_can_be_edited_alone_or_together_and_the_others_stay(client):
    student, _, advising = advised_pair()
    meeting = make_meeting(advising, author=student.user, scheduled_at=when(days=3, hour=10))
    target = when(days=5, hour=15, minute=30)

    only_date = edit(client, student.user, meeting.id, {"date": date_of(target)})
    only_time = edit(client, student.user, meeting.id, {"time": "15:30"})
    description = edit(client, student.user, meeting.id, {"description": "Pauta final"})

    assert only_date.json()["scheduled_at"] == when(days=5, hour=10).isoformat()
    assert only_time.json()["scheduled_at"] == target.isoformat()
    assert description.json()["scheduled_at"] == target.isoformat()
    meeting.refresh_from_db()
    assert (meeting.scheduled_at, meeting.description) == (target, "Pauta final")


def test_the_author_may_be_the_teacher_too(client):
    student, teacher, advising = advised_pair()
    meeting = make_meeting(advising, author=teacher.user, scheduled_at=when())

    assert edit(client, teacher.user, meeting.id, {"description": "Pauta"}).status_code == 200
    assert edit(client, student.user, meeting.id, {"description": "Outra"}).status_code == 404


def test_the_description_is_trimmed(client):
    student, _, advising = advised_pair()
    meeting = make_meeting(advising, author=student.user, scheduled_at=when())

    response = edit(client, student.user, meeting.id, {"description": "  Pauta \n"})

    assert response.json()["description"] == "Pauta"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"status": "accepted"},
        {"date": None},
        {"time": None},
        {"description": None},
        {"description": ""},
        {"description": "   "},
        {"description": "x" * 501},
        {"date": "amanhã"},
        {"date": "2099-02-30"},
        {"time": "25:00"},
        {"time": "14h30"},
        {"date": ""},
        {"time": ""},
    ],
)
def test_an_invalid_or_empty_body_is_refused_and_changes_nothing(client, body):
    student, _, advising = advised_pair()
    meeting = make_meeting(advising, author=student.user, scheduled_at=when())

    response = edit(client, student.user, meeting.id, body)

    assert response.status_code == 422
    meeting.refresh_from_db()
    assert (meeting.scheduled_at, meeting.description) == (when(), "Reunião de orientação")


@pytest.mark.parametrize("description", ["x", "x" * 500])
def test_the_limits_of_the_description_are_accepted(client, description):
    student, _, advising = advised_pair()
    meeting = make_meeting(advising, author=student.user, scheduled_at=when())

    assert edit(client, student.user, meeting.id, {"description": description}).status_code == 200


def test_the_status_cannot_be_changed_through_the_edit(client):
    student, _, advising = advised_pair()
    meeting = make_meeting(advising, author=student.user, scheduled_at=when())

    response = edit(client, student.user, meeting.id, {"description": "Nova", "status": "accepted"})

    assert response.status_code == 200
    assert Meeting.objects.get(pk=meeting.pk).status == MeetingStatus.PENDING


def test_a_new_date_or_time_in_the_past_is_refused_and_one_that_did_not_change_is_not_checked(
    client,
):
    student, _, advising = advised_pair()
    meeting = make_meeting(advising, author=student.user, scheduled_at=when(days=3, hour=10))
    past = timezone.now() - datetime.timedelta(days=1)

    refused = edit(client, student.user, meeting.id, {"date": date_of(past)})
    same = edit(
        client,
        student.user,
        meeting.id,
        {"date": date_of(when(days=3)), "time": "10:00", "description": "Pauta"},
    )

    assert refused.status_code == 422
    assert "date" in [error["loc"][-1] for error in refused.json()["detail"]]
    assert same.status_code == 200
    meeting.refresh_from_db()
    assert (meeting.scheduled_at, meeting.description) == (when(days=3, hour=10), "Pauta")


def test_moving_the_time_to_an_earlier_moment_of_today_that_already_passed_is_refused(client):
    student, _, advising = advised_pair()
    in_two_hours = timezone.now() + datetime.timedelta(hours=2)
    meeting = make_meeting(advising, author=student.user, scheduled_at=in_two_hours)

    response = edit(
        client,
        student.user,
        meeting.id,
        {"date": date_of(timezone.now() - datetime.timedelta(days=2))},
    )

    assert response.status_code == 422


def test_changing_the_date_or_the_time_of_an_accepted_meeting_sends_it_back_to_pending(client):
    student, teacher, advising = advised_pair()
    by_date = make_meeting(
        advising, author=student.user, status=MeetingStatus.ACCEPTED, scheduled_at=when(days=3)
    )
    by_time = make_meeting(
        advising, author=student.user, status=MeetingStatus.ACCEPTED, scheduled_at=when(days=3)
    )

    moved = edit(client, student.user, by_date.id, {"date": date_of(when(days=6))})
    retimed = edit(client, student.user, by_time.id, {"time": "17:45"})

    assert moved.json()["status"] == "pending"
    assert retimed.json()["status"] == "pending"
    assert Meeting.objects.get(pk=by_date.pk).status == MeetingStatus.PENDING
    assert Meeting.objects.get(pk=by_time.pk).status == MeetingStatus.PENDING


def test_changing_only_the_description_keeps_an_accepted_meeting_accepted(client):
    student, _, advising = advised_pair()
    meeting = make_meeting(
        advising, author=student.user, status=MeetingStatus.ACCEPTED, scheduled_at=when()
    )

    response = edit(client, student.user, meeting.id, {"description": "Pauta"})

    assert response.json()["status"] == "accepted"
    assert Meeting.objects.get(pk=meeting.pk).status == MeetingStatus.ACCEPTED


def test_an_accepted_meeting_edited_with_the_same_date_and_time_stays_accepted(client):
    student, _, advising = advised_pair()
    meeting = make_meeting(
        advising, author=student.user, status=MeetingStatus.ACCEPTED, scheduled_at=when(hour=10)
    )

    response = edit(
        client,
        student.user,
        meeting.id,
        {"date": date_of(when()), "time": "10:00", "description": "Pauta"},
    )

    assert response.json()["status"] == "accepted"


def test_a_pending_meeting_stays_pending_when_it_moves(client):
    student, _, advising = advised_pair()
    meeting = make_meeting(advising, author=student.user, scheduled_at=when())

    assert edit(client, student.user, meeting.id, {"time": "18:00"}).json()["status"] == "pending"


@pytest.mark.parametrize("status", [MeetingStatus.REJECTED, MeetingStatus.CANCELLED])
def test_a_rejected_or_cancelled_meeting_answers_409_on_edit(client, status):
    student, _, advising = advised_pair()
    meeting = make_meeting(advising, author=student.user, status=status, scheduled_at=when())
    if status == MeetingStatus.CANCELLED:
        Meeting.objects.filter(pk=meeting.pk).update(cancellation_reason="Imprevisto.")

    response = edit(client, student.user, meeting.id, {"description": "Pauta"})

    assert response.status_code == 409
    assert response.json() == {"detail": "Esta reunião não pode mais ser editada."}
    assert Meeting.objects.get(pk=meeting.pk).description == "Reunião de orientação"


@pytest.mark.parametrize("status", [MeetingStatus.PENDING, MeetingStatus.ACCEPTED])
def test_a_meeting_whose_time_passed_answers_409_on_edit(client, status):
    student, _, advising = advised_pair()
    meeting = make_meeting(
        advising,
        author=student.user,
        status=status,
        scheduled_at=timezone.now() - datetime.timedelta(minutes=1),
    )

    response = edit(client, student.user, meeting.id, {"description": "Pauta"})

    assert response.status_code == 409
    assert response.json() == {"detail": "O horário desta reunião já passou."}
    assert Meeting.objects.get(pk=meeting.pk).description == "Reunião de orientação"


def test_the_same_404_for_everything_the_user_may_not_edit(client):
    student, teacher, advising = advised_pair()
    own = make_meeting(advising, author=student.user, scheduled_at=when())
    other_student = make_student(course=student.course)
    other_advising = make_advising(
        student=other_student, teacher=make_teacher(courses=[student.course])
    )
    elsewhere = make_meeting(other_advising, author=other_student.user, scheduled_at=when())
    ended_student = make_student(course=student.course)
    ended_advising = make_advising(student=ended_student, teacher=teacher)
    on_ended = make_meeting(ended_advising, author=ended_student.user, scheduled_at=when())
    Advising.objects.filter(pk=ended_advising.pk).update(
        status=AdvisingStatus.ENDED, ended_at=timezone.now(), end_reason="Encerrada no teste."
    )
    body = {"description": "Pauta"}

    responses = [
        edit(client, teacher.user, own.id, body),
        edit(client, teacher.user, elsewhere.id, body),
        edit(client, ended_student.user, on_ended.id, body),
        edit(client, teacher.user, on_ended.id, body),
        edit(client, other_student.user, own.id, body),
        edit(client, student.user, 999999, body),
    ]

    assert {response.status_code for response in responses} == {404}
    assert len({response.content for response in responses}) == 1
    assert Meeting.objects.get(pk=own.pk).description == "Reunião de orientação"


def test_the_404_comes_before_the_409_so_nothing_is_revealed(client):
    student, teacher, advising = advised_pair()
    rejected = make_meeting(
        advising, author=student.user, status=MeetingStatus.REJECTED, scheduled_at=when()
    )
    stranger = make_teacher(courses=[student.course])

    assert edit(client, stranger.user, rejected.id, {"description": "x"}).status_code == 404
    assert edit(client, teacher.user, rejected.id, {"description": "x"}).status_code == 404


def test_an_invalid_body_is_validated_before_anything_is_looked_up(client):
    student, _, _ = advised_pair()

    assert edit(client, student.user, 999999, {}).status_code == 422
    assert edit(client, student.user, 999999, {"description": ""}).status_code == 422


def test_an_administrator_is_refused_and_anonymous_is_unauthorized(client):
    student, _, advising = advised_pair()
    meeting = make_meeting(advising, author=student.user, scheduled_at=when())

    assert (
        edit(client, make_user(UserType.ADMIN), meeting.id, {"description": "x"}).status_code == 403
    )
    assert (
        client.patch(
            url(meeting.id), data={"description": "x"}, content_type="application/json"
        ).status_code
        == 401
    )


def test_the_service_reports_what_changed_so_that_a_no_op_edit_can_be_told_apart(client):
    student, _, advising = advised_pair()
    meeting = make_meeting(
        advising, author=student.user, status=MeetingStatus.ACCEPTED, scheduled_at=when(hour=10)
    )
    new = when(days=4, hour=11)

    changed = edit_meeting(student.user, meeting.id, new.date(), new.time(), "Nova pauta")
    unchanged = edit_meeting(student.user, meeting.id, new.date(), new.time(), "Nova pauta")

    assert changed.changes == {
        "scheduled_at": FieldChange(old=when(hour=10), new=new),
        "description": FieldChange(old="Reunião de orientação", new="Nova pauta"),
        "status": FieldChange(old=MeetingStatus.ACCEPTED, new=MeetingStatus.PENDING),
    }
    assert unchanged.changes == {}


@pytest.mark.django_db(transaction=True)
def test_an_advising_that_ends_during_the_edit_gets_the_404_and_nothing_changes(client):
    student, _, advising = advised_pair()
    meeting = make_meeting(advising, author=student.user, scheduled_at=when())

    response = send_while_the_advising_ends(
        advising, lambda: edit(client, student.user, meeting.id, {"description": "Pauta"})
    )

    assert response.status_code == 404
    meeting.refresh_from_db()
    assert meeting.description == "Reunião de orientação"
    assert meeting.status == MeetingStatus.CANCELLED
    assert meeting.cancelled_by_system is True


def count_queries(client, user, meeting_id):
    with CaptureQueriesContext(connection) as queries:
        response = edit(client, user, meeting_id, {"description": f"Pauta {meeting_id}"})
    assert response.status_code == 200
    return len(queries)


def test_the_number_of_queries_does_not_depend_on_the_data_around(client):
    student, _, advising = advised_pair()
    baseline = count_queries(
        client, student.user, make_meeting(advising, author=student.user, scheduled_at=when()).id
    )
    for _ in range(4):
        other_student, _, other_advising = advised_pair()
        for _ in range(3):
            make_meeting(other_advising, author=other_student.user, scheduled_at=when())
        make_meeting(advising, author=student.user, scheduled_at=when())

    assert (
        count_queries(
            client,
            student.user,
            make_meeting(advising, author=student.user, scheduled_at=when()).id,
        )
        == baseline
    )


def test_the_openapi_documents_the_route_and_its_errors(client):
    operation = client.get("/api/openapi.json").json()["paths"]["/api/reunioes/{meeting_id}"][
        "patch"
    ]

    assert {"200", "401", "403", "404", "409", "422"} <= set(operation["responses"])
