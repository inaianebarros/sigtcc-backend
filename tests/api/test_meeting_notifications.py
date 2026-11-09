"""The notifications of the meeting events: creation (RF37, RF40) and answer (RF38, RF41)."""

import datetime
from unittest.mock import patch

import pytest
from django.test import Client

from apps.meetings.models import Meeting, MeetingStatus
from apps.notifications.models import Notification
from tests.api.helpers import CREATE_NOTIFICATION, bearer, inbox, post_json
from tests.factories import make_advising, make_course, make_meeting, make_student, make_teacher

pytestmark = pytest.mark.django_db

WHEN = "2099-12-31T14:30:00-03:00"


def advised_pair():
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    return student, teacher, make_advising(student=student, teacher=teacher)


def create_meeting(client, author, **extra):
    body = {
        "date": "2099-12-31",
        "time": "14:30",
        "description": "Revisar o capítulo 2 do TCC",
        **extra,
    }
    return post_json(client, "/api/reunioes", body, headers=bearer(author))


def test_a_student_requesting_a_meeting_notifies_only_the_teacher(client):
    student, teacher, _ = advised_pair()

    response = create_meeting(client, student.user)

    assert response.status_code == 201
    (item,) = inbox(client, teacher.user)
    assert item["type"] == "meeting_requested"
    assert item["reference"] == {"type": "meeting", "id": response.json()["id"]}
    assert item["is_read"] is False
    assert item["payload"] == {
        "author_name": student.user.full_name,
        "author_type": "student",
        "scheduled_at": WHEN,
        "description": "Revisar o capítulo 2 do TCC",
    }
    assert item["message"] == (
        f"{student.user.full_name} solicitou uma reunião para 31/12/2099 às 14:30."
    )
    assert inbox(client, student.user) == []
    assert Notification.objects.count() == 1


def test_a_teacher_scheduling_a_meeting_notifies_only_the_student(client):
    student, teacher, _ = advised_pair()

    response = create_meeting(client, teacher.user, student_id=student.user_id)

    assert response.status_code == 201
    (item,) = inbox(client, student.user)
    assert item["type"] == "meeting_requested"
    assert item["reference"] == {"type": "meeting", "id": response.json()["id"]}
    assert item["payload"] == {
        "author_name": teacher.user.full_name,
        "author_type": "teacher",
        "scheduled_at": WHEN,
        "description": "Revisar o capítulo 2 do TCC",
    }
    assert item["message"] == (
        f"{teacher.user.full_name} marcou uma reunião com você para 31/12/2099 às 14:30."
    )
    assert inbox(client, teacher.user) == []
    assert Notification.objects.count() == 1


def test_a_creation_that_fails_notifies_nobody(client):
    student, teacher, advising = advised_pair()
    loner = make_student()
    other_teacher = make_teacher(courses=[make_course()])

    invalid = create_meeting(client, student.user, time="25:00")
    no_advising = create_meeting(client, loner.user)
    unknown_student = create_meeting(client, other_teacher.user, student_id=student.user_id)

    assert invalid.status_code == 422
    assert no_advising.status_code == 409
    assert unknown_student.status_code == 404
    assert not Notification.objects.exists()


@pytest.mark.parametrize("author", ["student", "teacher"])
def test_a_failing_notification_rolls_the_creation_back(author):
    student, teacher, _ = advised_pair()
    quiet_client = Client(raise_request_exception=False)
    extra = {"student_id": student.user_id} if author == "teacher" else {}
    user = student.user if author == "student" else teacher.user

    with patch(CREATE_NOTIFICATION, side_effect=RuntimeError("boom")):
        response = create_meeting(quiet_client, user, **extra)

    assert response.status_code == 500
    assert not Meeting.objects.exists()
    assert not Notification.objects.exists()


def meeting_by(advising, author):
    return make_meeting(
        advising,
        author=author,
        scheduled_at=datetime.datetime(2099, 12, 31, 17, 30, tzinfo=datetime.UTC),
    )


def answer(client, user, meeting, action, **extra):
    return post_json(
        client,
        f"/api/reunioes/{meeting.id}/responder",
        {"action": action, **extra},
        headers=bearer(user),
    )


def test_the_teacher_accepting_a_students_meeting_notifies_the_student(client):
    student, teacher, advising = advised_pair()
    meeting = meeting_by(advising, student.user)

    response = answer(client, teacher.user, meeting, "accept")

    assert response.status_code == 200
    (item,) = inbox(client, student.user)
    assert item["type"] == "meeting_answered"
    assert item["reference"] == {"type": "meeting", "id": meeting.id}
    assert item["is_read"] is False
    assert item["payload"] == {
        "responder_name": teacher.user.full_name,
        "decision": "accepted",
        "scheduled_at": WHEN,
    }
    assert item["message"] == (
        f"{teacher.user.full_name} aceitou a reunião de 31/12/2099 às 14:30."
    )
    assert inbox(client, teacher.user) == []
    assert Notification.objects.count() == 1


def test_the_teacher_rejecting_carries_the_justification(client):
    student, teacher, advising = advised_pair()
    meeting = meeting_by(advising, student.user)

    answer(client, teacher.user, meeting, "reject", reason="Estarei em viagem.")

    (item,) = inbox(client, student.user)
    assert item["payload"] == {
        "responder_name": teacher.user.full_name,
        "decision": "rejected",
        "reason": "Estarei em viagem.",
        "scheduled_at": WHEN,
    }
    assert item["message"] == (
        f"{teacher.user.full_name} recusou a reunião de 31/12/2099 às 14:30. "
        "Justificativa: Estarei em viagem."
    )


@pytest.mark.parametrize(
    ("action", "extra", "verb"),
    [("accept", {}, "aceitou"), ("reject", {"reason": "Tenho prova."}, "recusou")],
)
def test_the_student_answering_a_teachers_meeting_notifies_the_teacher(client, action, extra, verb):
    student, teacher, advising = advised_pair()
    meeting = meeting_by(advising, teacher.user)

    response = answer(client, student.user, meeting, action, **extra)

    assert response.status_code == 200
    (item,) = inbox(client, teacher.user)
    assert item["type"] == "meeting_answered"
    assert item["reference"] == {"type": "meeting", "id": meeting.id}
    assert item["message"].startswith(
        f"{student.user.full_name} {verb} a reunião de 31/12/2099 às 14:30."
    )
    assert inbox(client, student.user) == []
    assert Notification.objects.count() == 1


def test_an_answer_that_fails_notifies_nobody(client):
    student, teacher, advising = advised_pair()
    meeting = meeting_by(advising, student.user)
    own = answer(client, student.user, meeting, "accept")
    no_reason = answer(client, teacher.user, meeting, "reject")
    first = answer(client, teacher.user, meeting, "accept")
    Notification.objects.all().delete()
    again = answer(client, teacher.user, meeting, "accept")

    assert own.status_code == 404
    assert no_reason.status_code == 422
    assert first.status_code == 200
    assert again.status_code == 409
    assert not Notification.objects.exists()


def test_a_failing_notification_rolls_the_answer_back():
    student, teacher, advising = advised_pair()
    meeting = meeting_by(advising, student.user)
    quiet_client = Client(raise_request_exception=False)

    with patch(CREATE_NOTIFICATION, side_effect=RuntimeError("boom")):
        response = answer(quiet_client, teacher.user, meeting, "accept")

    assert response.status_code == 500
    meeting.refresh_from_db()
    assert meeting.status == MeetingStatus.PENDING
    assert not Notification.objects.exists()


def test_editing_and_cancelling_a_meeting_do_not_notify(client):
    student, teacher, advising = advised_pair()
    meeting = meeting_by(advising, student.user)

    edited = client.patch(
        f"/api/reunioes/{meeting.id}",
        data={"description": "Nova pauta"},
        content_type="application/json",
        headers=bearer(student.user),
    )
    cancelled = post_json(
        client,
        f"/api/reunioes/{meeting.id}/cancelar",
        {"reason": "Imprevisto."},
        headers=bearer(student.user),
    )

    assert edited.status_code == 200
    assert cancelled.status_code == 200
    assert not Notification.objects.exists()
