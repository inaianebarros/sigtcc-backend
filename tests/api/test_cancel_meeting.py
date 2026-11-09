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

REASON = "Tive um imprevisto e não poderei comparecer."


def url(meeting_id):
    return f"/api/reunioes/{meeting_id}/cancelar"


def cancel(client, user, meeting_id, reason=REASON):
    return post_json(client, url(meeting_id), {"reason": reason}, headers=bearer(user))


def advised_pair():
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    return student, teacher, make_advising(student=student, teacher=teacher)


def test_the_author_cancels_a_pending_meeting_and_status_and_reason_are_stored_together(client):
    student, _, advising = advised_pair()
    meeting = make_meeting(advising, author=student.user)

    response = cancel(client, student.user, meeting.id)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "id",
        "scheduled_at",
        "description",
        "status",
        "author",
        "cancellation_reason",
        "cancelled_by_system",
    }
    assert body["status"] == "cancelled"
    assert body["cancellation_reason"] == REASON
    assert body["cancelled_by_system"] is False
    assert body["scheduled_at"].endswith("-03:00")
    meeting.refresh_from_db()
    assert meeting.status == MeetingStatus.CANCELLED
    assert meeting.cancellation_reason == REASON
    assert meeting.cancelled_by_system is False


def test_either_party_cancels_an_accepted_meeting(client):
    student, teacher, advising = advised_pair()
    by_the_author = make_meeting(advising, author=student.user, status=MeetingStatus.ACCEPTED)
    by_the_other = make_meeting(advising, author=student.user, status=MeetingStatus.ACCEPTED)
    by_the_student = make_meeting(advising, author=teacher.user, status=MeetingStatus.ACCEPTED)

    assert cancel(client, student.user, by_the_author.id).status_code == 200
    assert cancel(client, teacher.user, by_the_other.id).status_code == 200
    assert cancel(client, student.user, by_the_student.id).status_code == 200
    assert Meeting.objects.filter(status=MeetingStatus.CANCELLED).count() == 3


def test_only_the_author_cancels_a_pending_meeting_and_the_other_party_gets_404(client):
    student, teacher, advising = advised_pair()
    from_the_student = make_meeting(advising, author=student.user)
    from_the_teacher = make_meeting(advising, author=teacher.user)

    assert cancel(client, teacher.user, from_the_student.id).status_code == 404
    assert cancel(client, student.user, from_the_teacher.id).status_code == 404
    for meeting in (from_the_student, from_the_teacher):
        meeting.refresh_from_db()
        assert meeting.status == MeetingStatus.PENDING
        assert meeting.cancellation_reason == ""


def test_the_reason_is_trimmed(client):
    student, _, advising = advised_pair()
    meeting = make_meeting(advising, author=student.user)

    response = cancel(client, student.user, meeting.id, reason="  Viagem de última hora. \n")

    assert response.json()["cancellation_reason"] == "Viagem de última hora."


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"reason": None},
        {"reason": ""},
        {"reason": "   "},
        {"reason": "\n\t "},
        {"reason": "x" * 501},
        {"reason": " " + "x" * 501 + " "},
    ],
)
def test_the_reason_is_required_and_an_invalid_one_changes_nothing(client, body):
    student, _, advising = advised_pair()
    meeting = make_meeting(advising, author=student.user)

    response = post_json(client, url(meeting.id), body, headers=bearer(student.user))

    assert response.status_code == 422
    assert "reason" in [error["loc"][-1] for error in response.json()["detail"]]
    meeting.refresh_from_db()
    assert meeting.status == MeetingStatus.PENDING
    assert meeting.cancellation_reason == ""


@pytest.mark.parametrize("reason", ["x", "x" * 500])
def test_the_limits_of_the_reason_are_accepted(client, reason):
    student, _, advising = advised_pair()
    meeting = make_meeting(advising, author=student.user)

    assert cancel(client, student.user, meeting.id, reason=reason).status_code == 200


def test_a_rejected_meeting_cannot_be_cancelled(client):
    student, teacher, advising = advised_pair()
    meeting = make_meeting(advising, author=student.user, status=MeetingStatus.REJECTED)

    for user in (student.user, teacher.user):
        response = cancel(client, user, meeting.id)
        assert response.status_code == 409
        assert response.json() == {"detail": "Esta reunião foi recusada e não pode ser cancelada."}
    assert Meeting.objects.get(pk=meeting.pk).status == MeetingStatus.REJECTED


def test_a_cancelled_meeting_cannot_be_cancelled_again_even_by_the_system(client):
    student, teacher, advising = advised_pair()
    by_hand = make_meeting(advising, author=student.user, status=MeetingStatus.CANCELLED)
    by_system = make_meeting(advising, author=student.user, status=MeetingStatus.CANCELLED)
    Meeting.objects.filter(pk=by_hand.pk).update(cancellation_reason="Imprevisto.")
    Meeting.objects.filter(pk=by_system.pk).update(
        cancellation_reason="Cancelada pelo sistema.", cancelled_by_system=True
    )

    for meeting in (by_hand, by_system):
        for user in (student.user, teacher.user):
            response = cancel(client, user, meeting.id)
            assert response.status_code == 409
            assert response.json() == {"detail": "Esta reunião já foi cancelada."}
    assert Meeting.objects.get(pk=by_system.pk).cancellation_reason == "Cancelada pelo sistema."
    assert Meeting.objects.get(pk=by_system.pk).cancelled_by_system is True


@pytest.mark.parametrize("status", [MeetingStatus.PENDING, MeetingStatus.ACCEPTED])
def test_a_meeting_whose_time_passed_cannot_be_cancelled(client, status):
    student, _, advising = advised_pair()
    meeting = make_meeting(
        advising,
        author=student.user,
        status=status,
        scheduled_at=timezone.now() - datetime.timedelta(minutes=1),
    )

    response = cancel(client, student.user, meeting.id)

    assert response.status_code == 409
    assert response.json() == {"detail": "O horário desta reunião já passou."}
    assert Meeting.objects.get(pk=meeting.pk).status == status


def test_the_same_404_for_everything_the_user_may_not_cancel(client):
    student, teacher, advising = advised_pair()
    pending = make_meeting(advising, author=student.user)
    other_student = make_student(course=student.course)
    other_advising = make_advising(
        student=other_student, teacher=make_teacher(courses=[student.course])
    )
    elsewhere = make_meeting(
        other_advising, author=other_student.user, status=MeetingStatus.ACCEPTED
    )
    ended_student = make_student(course=student.course)
    ended_advising = make_advising(student=ended_student, teacher=teacher)
    on_ended = make_meeting(
        ended_advising, author=ended_student.user, status=MeetingStatus.ACCEPTED
    )
    Advising.objects.filter(pk=ended_advising.pk).update(
        status=AdvisingStatus.ENDED, ended_at=timezone.now(), end_reason="Encerrada no teste."
    )

    responses = [
        cancel(client, teacher.user, pending.id),
        cancel(client, teacher.user, elsewhere.id),
        cancel(client, ended_student.user, on_ended.id),
        cancel(client, teacher.user, on_ended.id),
        cancel(client, other_student.user, pending.id),
        cancel(client, student.user, 999999),
    ]

    assert {response.status_code for response in responses} == {404}
    assert len({response.content for response in responses}) == 1
    assert Meeting.objects.get(pk=pending.pk).status == MeetingStatus.PENDING
    assert Meeting.objects.get(pk=elsewhere.pk).status == MeetingStatus.ACCEPTED


def test_an_invalid_body_is_validated_before_anything_is_looked_up(client):
    student, _, _ = advised_pair()

    assert cancel(client, student.user, 999999, reason="").status_code == 422


def test_an_administrator_is_refused_and_anonymous_is_unauthorized(client):
    student, _, advising = advised_pair()
    meeting = make_meeting(advising, author=student.user)

    assert cancel(client, make_user(UserType.ADMIN), meeting.id).status_code == 403
    assert post_json(client, url(meeting.id), {"reason": REASON}).status_code == 401
    assert Meeting.objects.get(pk=meeting.pk).status == MeetingStatus.PENDING


@pytest.mark.django_db(transaction=True)
def test_an_advising_that_ends_during_the_cancellation_gets_the_404_and_the_reason_is_not_stored(
    client,
):
    student, _, advising = advised_pair()
    meeting = make_meeting(advising, author=student.user)

    response = send_while_the_advising_ends(
        advising, lambda: cancel(client, student.user, meeting.id)
    )

    assert response.status_code == 404
    meeting.refresh_from_db()
    # O encerramento cancelou a reunião (RN34); o motivo do autor não foi gravado.
    assert meeting.status == MeetingStatus.CANCELLED
    assert meeting.cancelled_by_system is True
    assert meeting.cancellation_reason != REASON


def count_queries(client, user, meeting_id):
    with CaptureQueriesContext(connection) as queries:
        response = cancel(client, user, meeting_id)
    assert response.status_code == 200
    return len(queries)


def test_the_number_of_queries_does_not_depend_on_the_data_around(client):
    student, _, advising = advised_pair()
    baseline = count_queries(client, student.user, make_meeting(advising, author=student.user).id)
    for _ in range(4):
        other_student, _, other_advising = advised_pair()
        for _ in range(3):
            make_meeting(other_advising, author=other_student.user)
        make_meeting(advising, author=student.user)

    assert (
        count_queries(client, student.user, make_meeting(advising, author=student.user).id)
        == baseline
    )


def test_the_openapi_documents_the_route_and_its_errors(client):
    operation = client.get("/api/openapi.json").json()["paths"][
        "/api/reunioes/{meeting_id}/cancelar"
    ]["post"]

    assert {"200", "401", "403", "404", "409", "422"} <= set(operation["responses"])
