from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import UserType
from apps.advising.deadlines import answer_cutoff
from apps.advising.models import Advising, AdvisingRequest, AdvisingRequestStatus, AdvisingStatus
from tests.api.helpers import bearer
from tests.factories import (
    make_advising,
    make_advising_request,
    make_course,
    make_student,
    make_teacher,
    make_user,
)

pytestmark = pytest.mark.django_db

STATUS_URL = "/api/orientacao/minha-solicitacao"


def read_status(client, user):
    return client.get(STATUS_URL, headers=bearer(user))


def make_student_and_teacher():
    course = make_course()
    return make_student(course=course), make_teacher(courses=[course])


def backdate_request(advising_request, **delta):
    AdvisingRequest.objects.filter(pk=advising_request.pk).update(
        requested_at=timezone.now() - timedelta(**delta)
    )


def teacher_summary(teacher):
    return {"id": teacher.user_id, "full_name": teacher.user.full_name}


def test_a_student_who_never_requested_can_request(client):
    student = make_student()

    response = read_status(client, student.user)

    assert response.status_code == 200
    assert response.json() == {
        "request": None,
        "advising": None,
        "can_request": True,
        "blocked_reason": None,
    }


def test_a_pending_request_within_the_deadline_blocks_a_new_one(client):
    student, teacher = make_student_and_teacher()
    pending = make_advising_request(student=student, teacher=teacher)
    backdate_request(pending, days=1)
    pending.refresh_from_db()

    body = read_status(client, student.user).json()

    assert body["can_request"] is False
    assert body["blocked_reason"] == "pending_request"
    assert body["advising"] is None
    assert body["request"]["id"] == pending.id
    assert body["request"]["status"] == "pending"
    assert body["request"]["teacher"] == teacher_summary(teacher)
    assert body["request"]["responded_at"] is None
    expires_at = datetime.fromisoformat(body["request"]["expires_at"])
    assert abs(expires_at - pending.requested_at - timedelta(days=7)) < timedelta(milliseconds=1)


def test_a_pending_request_older_than_seven_days_shows_as_unanswered_and_does_not_block(client):
    student, teacher = make_student_and_teacher()
    overdue = make_advising_request(student=student, teacher=teacher)
    backdate_request(overdue, days=8)

    body = read_status(client, student.user).json()

    assert body["request"]["status"] == "unanswered"
    assert body["request"]["expires_at"] is not None
    assert body["can_request"] is True
    assert body["blocked_reason"] is None
    overdue.refresh_from_db()
    assert overdue.status == AdvisingRequestStatus.PENDING


def test_an_active_advising_blocks_and_wins_over_a_pending_request(client):
    student, teacher = make_student_and_teacher()
    advising = make_advising(student=student, teacher=teacher)
    make_advising_request(student=student, teacher=make_teacher(courses=[student.course]))

    body = read_status(client, student.user).json()

    assert body["can_request"] is False
    assert body["blocked_reason"] == "active_advising"
    assert body["advising"]["id"] == advising.id
    assert body["advising"]["teacher"] == teacher_summary(teacher)
    assert body["advising"]["started_at"] is not None


@pytest.mark.parametrize(
    "previous_status", [AdvisingRequestStatus.REJECTED, AdvisingRequestStatus.UNANSWERED]
)
def test_after_a_refusal_or_expiry_the_student_can_request_again(client, previous_status):
    student, teacher = make_student_and_teacher()
    previous = make_advising_request(student=student, teacher=teacher)
    AdvisingRequest.objects.filter(pk=previous.pk).update(status=previous_status)

    body = read_status(client, student.user).json()

    assert body["request"]["status"] == previous_status.value
    assert body["can_request"] is True
    assert body["blocked_reason"] is None


def test_an_accepted_request_has_no_deadline(client):
    student, teacher = make_student_and_teacher()
    accepted = make_advising_request(student=student, teacher=teacher)
    AdvisingRequest.objects.filter(pk=accepted.pk).update(
        status=AdvisingRequestStatus.ACCEPTED, responded_at=timezone.now()
    )
    make_advising(student=student, teacher=teacher)

    body = read_status(client, student.user).json()

    assert body["request"]["status"] == "accepted"
    assert body["request"]["expires_at"] is None
    assert body["request"]["responded_at"] is not None


def test_after_the_advising_ends_the_request_stays_accepted_and_the_student_can_request(client):
    student, teacher = make_student_and_teacher()
    accepted = make_advising_request(student=student, teacher=teacher)
    AdvisingRequest.objects.filter(pk=accepted.pk).update(status=AdvisingRequestStatus.ACCEPTED)
    ended = make_advising(student=student, teacher=teacher)
    Advising.objects.filter(pk=ended.pk).update(
        status=AdvisingStatus.ENDED, ended_at=timezone.now(), end_reason="Encerrada no teste."
    )

    body = read_status(client, student.user).json()

    assert body["request"]["status"] == "accepted"
    assert body["advising"] is None
    assert body["can_request"] is True
    assert body["blocked_reason"] is None


def test_the_most_recent_request_is_the_one_returned(client):
    student, teacher = make_student_and_teacher()
    older = make_advising_request(student=student, teacher=teacher)
    AdvisingRequest.objects.filter(pk=older.pk).update(status=AdvisingRequestStatus.REJECTED)
    backdate_request(older, days=3)
    newer = make_advising_request(student=student, teacher=make_teacher(courses=[student.course]))

    body = read_status(client, student.user).json()

    assert body["request"]["id"] == newer.id
    assert body["blocked_reason"] == "pending_request"


def test_a_student_never_sees_another_students_request_or_advising(client):
    student, _ = make_student_and_teacher()
    other, teacher = make_student_and_teacher()
    make_advising_request(student=other, teacher=teacher)
    make_advising(student=other, teacher=teacher)

    body = read_status(client, student.user).json()

    assert body == {"request": None, "advising": None, "can_request": True, "blocked_reason": None}


def count_queries(client, user):
    headers = bearer(user)
    with CaptureQueriesContext(connection) as queries:
        response = client.get(STATUS_URL, headers=headers)
    assert response.status_code == 200
    return [query["sql"] for query in queries]


def test_reading_the_status_writes_nothing_and_does_not_grow_with_the_history(client):
    student, teacher = make_student_and_teacher()
    first = make_advising_request(student=student, teacher=teacher)
    AdvisingRequest.objects.filter(pk=first.pk).update(status=AdvisingRequestStatus.REJECTED)
    backdate_request(first, days=30)
    few = count_queries(client, student.user)

    for days in (20, 15, 10):
        extra = make_advising_request(student=student, teacher=teacher)
        AdvisingRequest.objects.filter(pk=extra.pk).update(status=AdvisingRequestStatus.REJECTED)
        backdate_request(extra, days=days)
    overdue = make_advising_request(student=student, teacher=teacher)
    backdate_request(overdue, days=9)
    snapshot = list(AdvisingRequest.objects.order_by("id").values_list("id", "status"))
    many = count_queries(client, student.user)

    assert len(many) == len(few)
    writes = [
        sql for sql in many if sql.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE"))
    ]
    assert writes == []
    assert list(AdvisingRequest.objects.order_by("id").values_list("id", "status")) == snapshot


def test_one_clock_reading_decides_both_the_displayed_status_and_the_eligibility(client):
    student, teacher = make_student_and_teacher()
    make_advising_request(student=student, teacher=teacher)
    headers = bearer(student.user)

    with (
        patch("apps.advising.services.answer_cutoff", wraps=answer_cutoff) as service_reading,
        patch("apps.advising.models.answer_cutoff", wraps=answer_cutoff) as model_reading,
    ):
        client.get(STATUS_URL, headers=headers)

    assert service_reading.call_count + model_reading.call_count == 1


def test_requests_with_the_same_timestamp_return_the_one_with_the_highest_id(client):
    student, teacher = make_student_and_teacher()
    moment = timezone.now() - timedelta(days=30)
    created = []
    for _ in range(3):
        request = make_advising_request(student=student, teacher=teacher)
        AdvisingRequest.objects.filter(pk=request.pk).update(
            status=AdvisingRequestStatus.REJECTED, requested_at=moment
        )
        created.append(request.id)

    body = read_status(client, student.user).json()

    assert body["request"]["id"] == max(created)


def test_after_an_ended_advising_a_new_pending_request_blocks_again(client):
    student, teacher = make_student_and_teacher()
    ended = make_advising(student=student, teacher=teacher)
    Advising.objects.filter(pk=ended.pk).update(
        status=AdvisingStatus.ENDED, ended_at=timezone.now(), end_reason="Encerrada no teste."
    )
    newer = make_advising_request(student=student, teacher=make_teacher(courses=[student.course]))

    body = read_status(client, student.user).json()

    assert body["advising"] is None
    assert body["request"]["id"] == newer.id
    assert body["blocked_reason"] == "pending_request"


def test_the_accepted_request_of_one_teacher_and_the_active_advising_of_another_are_both_shown(
    client,
):
    student, first_teacher = make_student_and_teacher()
    accepted = make_advising_request(student=student, teacher=first_teacher)
    AdvisingRequest.objects.filter(pk=accepted.pk).update(status=AdvisingRequestStatus.ACCEPTED)
    backdate_request(accepted, days=20)
    second_teacher = make_teacher(courses=[student.course])
    make_advising(student=student, teacher=second_teacher)

    body = read_status(client, student.user).json()

    assert body["request"]["teacher"] == teacher_summary(first_teacher)
    assert body["advising"]["teacher"] == teacher_summary(second_teacher)
    assert body["blocked_reason"] == "active_advising"


def test_only_students_can_read_it_and_visitors_are_not_authenticated(client):
    teacher = make_teacher()
    admin = make_user(UserType.ADMIN)

    assert read_status(client, teacher.user).status_code == 403
    assert read_status(client, admin).status_code == 403
    assert client.get(STATUS_URL).status_code == 401


def test_an_inactive_student_is_not_authenticated(client):
    student = make_student()
    headers = bearer(student.user)
    student.user.is_active = False
    student.user.save(update_fields=["is_active"])

    assert client.get(STATUS_URL, headers=headers).status_code == 401


def test_the_openapi_documents_the_response(client):
    schema = client.get("/api/openapi.json").json()

    operation = schema["paths"]["/api/orientacao/minha-solicitacao"]["get"]
    assert "200" in operation["responses"]
    assert "RequestStatusOut" in schema["components"]["schemas"]
