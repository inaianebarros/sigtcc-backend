import datetime

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import UserType
from apps.advising.models import Advising, AdvisingStatus
from apps.meetings.models import Meeting, MeetingStatus
from tests.api.helpers import bearer, post_json, send_while_the_advising_ends
from tests.factories import (
    make_advising,
    make_course,
    make_meeting,
    make_student,
    make_teacher,
    make_user,
)

pytestmark = pytest.mark.django_db


def answer_url(meeting_id):
    return f"/api/reunioes/{meeting_id}/responder"


def answer(client, user, meeting_id, body):
    return post_json(client, answer_url(meeting_id), body, headers=bearer(user))


def accept(client, user, meeting_id):
    return answer(client, user, meeting_id, {"action": "accept"})


def reject(client, user, meeting_id, reason="Não tenho horário nesse dia."):
    return answer(client, user, meeting_id, {"action": "reject", "reason": reason})


def advised_pair():
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    return student, teacher, make_advising(student=student, teacher=teacher)


def test_the_teacher_accepts_a_meeting_the_student_requested(client):
    student, teacher, advising = advised_pair()
    meeting = make_meeting(advising, author=student.user)

    response = accept(client, teacher.user, meeting.id)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "id",
        "scheduled_at",
        "ends_at",
        "description",
        "status",
        "author",
        "rejection_reason",
    }
    assert body["id"] == meeting.id
    assert body["status"] == "accepted"
    assert body["rejection_reason"] == ""
    assert body["author"] == {
        "id": student.user_id,
        "full_name": student.user.full_name,
        "type": "student",
    }
    assert body["scheduled_at"].endswith("-03:00")
    meeting.refresh_from_db()
    assert meeting.status == MeetingStatus.ACCEPTED
    assert meeting.author == student.user
    assert Meeting.objects.count() == 1


def test_the_student_answers_a_meeting_the_teacher_requested(client):
    student, teacher, advising = advised_pair()
    accepted = make_meeting(advising, author=teacher.user)
    rejected = make_meeting(advising, author=teacher.user)

    assert accept(client, student.user, accepted.id).json()["status"] == "accepted"
    response = reject(client, student.user, rejected.id)

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["author"]["type"] == "teacher"


def test_the_rejection_stores_the_trimmed_reason_and_returns_it(client):
    student, teacher, advising = advised_pair()
    meeting = make_meeting(advising, author=student.user)

    response = reject(client, teacher.user, meeting.id, reason="  Estarei em viagem. \n")

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["rejection_reason"] == "Estarei em viagem."
    meeting.refresh_from_db()
    assert meeting.status == MeetingStatus.REJECTED
    assert meeting.rejection_reason == "Estarei em viagem."


def test_the_time_comes_back_in_santarem_and_the_meeting_keeps_what_it_had(client):
    student, teacher, advising = advised_pair()
    when = (timezone.now() + datetime.timedelta(days=2)).replace(microsecond=0)
    meeting = make_meeting(advising, author=student.user, scheduled_at=when)

    body = accept(client, teacher.user, meeting.id).json()

    assert datetime.datetime.fromisoformat(body["scheduled_at"]) == when
    assert body["scheduled_at"].endswith("-03:00")
    assert body["description"] == meeting.description


@pytest.mark.parametrize(
    "body",
    [
        {"action": "reject"},
        {"action": "reject", "reason": None},
        {"action": "reject", "reason": ""},
        {"action": "reject", "reason": "   "},
        {"action": "reject", "reason": "\n\t "},
        {"action": "reject", "reason": "x" * 501},
        {"action": "accept", "reason": "Pode ser."},
        {"action": "accept", "reason": ""},
        {"action": "accept", "reason": "   "},
        {"action": "talvez"},
        {"action": ""},
        {},
        {"reason": "Sem ação."},
    ],
)
def test_an_invalid_answer_is_refused_and_the_meeting_stays_pending(client, body):
    student, teacher, advising = advised_pair()
    meeting = make_meeting(advising, author=student.user)

    response = answer(client, teacher.user, meeting.id, body)

    assert response.status_code == 422
    meeting.refresh_from_db()
    assert meeting.status == MeetingStatus.PENDING
    assert meeting.rejection_reason == ""


@pytest.mark.parametrize("reason", ["x", "x" * 500])
def test_the_limits_of_the_reason_are_accepted(client, reason):
    student, teacher, advising = advised_pair()
    meeting = make_meeting(advising, author=student.user)

    assert reject(client, teacher.user, meeting.id, reason=reason).status_code == 200


def test_the_reason_error_is_on_the_reason_field(client):
    student, teacher, advising = advised_pair()
    meeting = make_meeting(advising, author=student.user)

    for body in ({"action": "reject"}, {"action": "accept", "reason": "Pode ser."}):
        response = answer(client, teacher.user, meeting.id, body)
        assert "reason" in [error["loc"][-1] for error in response.json()["detail"]]


def test_the_same_404_for_every_meeting_the_user_cannot_answer(client):
    student, teacher, advising = advised_pair()
    own = make_meeting(advising, author=student.user)
    other_teacher = make_teacher(courses=[student.course])
    other_student = make_student(course=student.course)
    other_advising = make_advising(student=other_student, teacher=other_teacher)
    elsewhere = make_meeting(other_advising, author=other_student.user)
    ended_student = make_student(course=student.course)
    ended_advising = make_advising(student=ended_student, teacher=teacher)
    on_ended = make_meeting(ended_advising, author=ended_student.user)
    Advising.objects.filter(pk=ended_advising.pk).update(
        status=AdvisingStatus.ENDED, ended_at=timezone.now(), end_reason="Encerrada no teste."
    )

    responses = [
        accept(client, student.user, own.id),
        reject(client, student.user, own.id),
        accept(client, teacher.user, elsewhere.id),
        accept(client, teacher.user, on_ended.id),
        accept(client, ended_student.user, on_ended.id),
        accept(client, other_student.user, own.id),
        accept(client, teacher.user, 999999),
        accept(client, student.user, 999999),
    ]

    assert {response.status_code for response in responses} == {404}
    assert len({response.content for response in responses}) == 1
    for meeting in (own, elsewhere, on_ended):
        meeting.refresh_from_db()
        assert meeting.status == MeetingStatus.PENDING


@pytest.mark.parametrize(
    "status",
    [MeetingStatus.ACCEPTED, MeetingStatus.REJECTED, MeetingStatus.CANCELLED],
)
def test_a_meeting_that_is_not_pending_answers_409_and_does_not_change(client, status):
    student, teacher, advising = advised_pair()
    meeting = make_meeting(advising, author=student.user, status=status)
    if status == MeetingStatus.CANCELLED:
        Meeting.objects.filter(pk=meeting.pk).update(cancellation_reason="Imprevisto.")

    responses = [accept(client, teacher.user, meeting.id), reject(client, teacher.user, meeting.id)]

    assert {response.status_code for response in responses} == {409}
    assert responses[0].json()["detail"] == (
        "Esta reunião foi cancelada."
        if status == MeetingStatus.CANCELLED
        else "Esta reunião já foi respondida."
    )
    assert Meeting.objects.get(pk=meeting.pk).status == status


def test_an_answer_is_final_and_a_second_answer_gets_409(client):
    student, teacher, advising = advised_pair()
    meeting = make_meeting(advising, author=student.user)

    assert accept(client, teacher.user, meeting.id).status_code == 200
    assert reject(client, teacher.user, meeting.id).status_code == 409
    assert Meeting.objects.get(pk=meeting.pk).status == MeetingStatus.ACCEPTED


def test_a_pending_meeting_whose_time_passed_answers_409_and_stays_pending(client):
    student, teacher, advising = advised_pair()
    meeting = make_meeting(
        advising,
        author=student.user,
        scheduled_at=timezone.now() - datetime.timedelta(minutes=1),
    )

    for call in (accept, reject):
        response = call(client, teacher.user, meeting.id)
        assert response.status_code == 409
        assert response.json() == {"detail": "O horário desta reunião já passou."}
    meeting.refresh_from_db()
    assert meeting.status == MeetingStatus.PENDING
    assert meeting.rejection_reason == ""


def test_the_404_comes_before_the_409_so_nothing_is_revealed(client):
    student, teacher, advising = advised_pair()
    answered = make_meeting(advising, author=student.user, status=MeetingStatus.ACCEPTED)
    stranger = make_teacher(courses=[student.course])

    assert accept(client, stranger.user, answered.id).status_code == 404
    assert accept(client, student.user, answered.id).status_code == 404


def test_an_invalid_body_is_validated_before_anything_is_looked_up(client):
    _, teacher, _ = advised_pair()

    assert answer(client, teacher.user, 999999, {"action": "reject"}).status_code == 422
    assert (
        answer(client, teacher.user, 999999, {"action": "accept", "reason": "x"}).status_code == 422
    )


def test_an_administrator_is_refused_and_anonymous_is_unauthorized(client):
    student, _, advising = advised_pair()
    meeting = make_meeting(advising, author=student.user)

    assert accept(client, make_user(UserType.ADMIN), meeting.id).status_code == 403
    assert post_json(client, answer_url(meeting.id), {"action": "accept"}).status_code == 401
    assert Meeting.objects.get(pk=meeting.pk).status == MeetingStatus.PENDING


@pytest.mark.parametrize("call", [accept, reject])
@pytest.mark.django_db(transaction=True)
def test_an_advising_that_ends_during_the_answer_gets_the_404_and_nothing_changes(client, call):
    student, teacher, advising = advised_pair()
    meeting = make_meeting(advising, author=student.user)

    response = send_while_the_advising_ends(
        advising, lambda: call(client, teacher.user, meeting.id)
    )

    assert response.status_code == 404
    meeting.refresh_from_db()
    # Encerrar a orientação cancela a reunião pendente (RN34): a resposta não valeu, nem aceita
    # nem recusada, e não deixou motivo de recusa.
    assert meeting.status == MeetingStatus.CANCELLED
    assert meeting.cancelled_by_system is True
    assert meeting.rejection_reason == ""
    assert Advising.objects.get(pk=advising.pk).status == AdvisingStatus.ENDED


def count_queries(client, user, meeting_id):
    with CaptureQueriesContext(connection) as queries:
        response = accept(client, user, meeting_id)
    assert response.status_code == 200
    return len(queries)


def test_the_number_of_queries_does_not_depend_on_the_data_around(client):
    student, teacher, advising = advised_pair()
    baseline = count_queries(client, teacher.user, make_meeting(advising, author=student.user).id)
    for _ in range(4):
        other_student, other_teacher, other_advising = advised_pair()
        for _ in range(3):
            make_meeting(other_advising, author=other_student.user)
        make_meeting(advising, author=student.user)

    assert (
        count_queries(client, teacher.user, make_meeting(advising, author=student.user).id)
        == baseline
    )


def test_the_openapi_documents_the_route_and_its_errors(client):
    operation = client.get("/api/openapi.json").json()["paths"][
        "/api/reunioes/{meeting_id}/responder"
    ]["post"]

    assert {"200", "401", "403", "404", "409", "422"} <= set(operation["responses"])
