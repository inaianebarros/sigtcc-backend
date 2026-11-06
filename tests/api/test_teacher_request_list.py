from datetime import datetime, timedelta

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.advising.models import AdvisingRequest, AdvisingRequestStatus
from tests.api.helpers import bearer
from tests.factories import (
    make_advising_request,
    make_area,
    make_course,
    make_student,
    make_teacher,
)

pytestmark = pytest.mark.django_db

REQUESTS_URL = "/api/orientacao/solicitacoes"


def list_requests(client, user, **params):
    return client.get(REQUESTS_URL, params, headers=bearer(user))


def test_a_teacher_lists_only_their_own_requests_newest_first_with_basic_student_data(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    older_student = make_student(course=course)
    newer_student = make_student(course=course)
    area = make_area()
    newer_student.areas_of_interest.add(area)
    older = make_advising_request(student=older_student, teacher=teacher)
    newer = make_advising_request(student=newer_student, teacher=teacher)
    make_advising_request(student=make_student(), teacher=make_teacher())

    response = list_requests(client, teacher.user)

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    assert [item["id"] for item in body["items"]] == [newer.id, older.id]
    assert body["items"][0] == {
        "id": newer.id,
        "status": "pending",
        "requested_at": body["items"][0]["requested_at"],
        "responded_at": None,
        "expires_at": body["items"][0]["expires_at"],
        "student": {
            "id": newer_student.user_id,
            "full_name": newer_student.user.full_name,
            "course": {"id": course.id, "name": course.name},
            "areas": [{"id": area.id, "name": area.name}],
        },
    }


def make_received(teacher, status=AdvisingRequestStatus.PENDING, days_ago=0):
    received = make_advising_request(student=make_student(), teacher=teacher)
    AdvisingRequest.objects.filter(pk=received.pk).update(
        status=status, requested_at=timezone.now() - timedelta(days=days_ago)
    )
    return received


def ids(response):
    return [item["id"] for item in response.json()["items"]]


def test_the_status_filter_returns_only_the_requested_status():
    teacher = make_teacher()
    expected = {
        status: make_received(teacher, status).id
        for status in (
            AdvisingRequestStatus.PENDING,
            AdvisingRequestStatus.ACCEPTED,
            AdvisingRequestStatus.REJECTED,
            AdvisingRequestStatus.UNANSWERED,
        )
    }

    for status, request_id in expected.items():
        response = list_requests(Client(), teacher.user, status=status.value)

        assert response.status_code == 200
        assert ids(response) == [request_id]
        assert response.json()["count"] == 1


def test_an_invalid_status_filter_is_rejected(client):
    teacher = make_teacher()

    response = list_requests(client, teacher.user, status="whatever")

    assert response.status_code == 422


def test_a_pending_request_older_than_seven_days_shows_and_filters_as_unanswered(client):
    teacher = make_teacher()
    fresh = make_received(teacher, days_ago=6)
    overdue = make_received(teacher, days_ago=8)
    stored_unanswered = make_received(teacher, AdvisingRequestStatus.UNANSWERED, days_ago=9)

    everything = list_requests(client, teacher.user)
    pending = list_requests(client, teacher.user, status="pending")
    unanswered = list_requests(client, teacher.user, status="unanswered")

    by_id = {item["id"]: item["status"] for item in everything.json()["items"]}
    assert by_id == {
        fresh.id: "pending",
        overdue.id: "unanswered",
        stored_unanswered.id: "unanswered",
    }
    assert ids(pending) == [fresh.id]
    assert pending.json()["count"] == 1
    assert set(ids(unanswered)) == {overdue.id, stored_unanswered.id}
    assert unanswered.json()["count"] == 2
    overdue.refresh_from_db()
    assert overdue.status == AdvisingRequestStatus.PENDING


def test_expires_at_is_seven_days_after_the_request(client):
    teacher = make_teacher()
    received = make_received(teacher, days_ago=1)
    received.refresh_from_db()

    item = list_requests(client, teacher.user).json()["items"][0]

    expires_at = datetime.fromisoformat(item["expires_at"])
    one_millisecond = timedelta(milliseconds=1)
    assert abs(expires_at - received.requested_at - timedelta(days=7)) < one_millisecond
    assert item["responded_at"] is None


def test_the_list_is_paginated_with_limit_and_offset(client):
    teacher = make_teacher()
    received = [make_received(teacher, days_ago=n) for n in range(25, 0, -1)]
    newest_first = [r.id for r in reversed(received)]

    default_page = list_requests(client, teacher.user)
    second_page = list_requests(client, teacher.user, limit=10, offset=10)
    beyond = list_requests(client, teacher.user, offset=100)

    assert default_page.json()["count"] == 25
    assert ids(default_page) == newest_first[:20]
    assert ids(second_page) == newest_first[10:20]
    assert ids(beyond) == []
    assert beyond.json()["count"] == 25


@pytest.mark.parametrize("params", [{"limit": 101}, {"limit": 0}, {"offset": -1}])
def test_the_page_size_is_capped_at_one_hundred(client, params):
    teacher = make_teacher()

    response = list_requests(client, teacher.user, **params)

    assert response.status_code == 422


def test_requests_with_the_same_timestamp_keep_a_stable_order(client):
    teacher = make_teacher()
    moment = timezone.now() - timedelta(days=1)
    created = [make_received(teacher) for _ in range(5)]
    AdvisingRequest.objects.filter(teacher=teacher).update(requested_at=moment)

    first = ids(list_requests(client, teacher.user))
    paged = ids(list_requests(client, teacher.user, limit=2)) + ids(
        list_requests(client, teacher.user, limit=3, offset=2)
    )

    assert first == sorted((r.id for r in created), reverse=True)
    assert paged == first


def test_the_number_of_queries_does_not_grow_with_the_number_of_requests(client):
    teacher = make_teacher()
    area = make_area()

    def count_queries():
        with CaptureQueriesContext(connection) as queries:
            response = list_requests(client, teacher.user)
        assert response.status_code == 200
        return len(queries)

    for _ in range(2):
        received = make_received(teacher)
        received.student.areas_of_interest.add(area)
    few = count_queries()
    for _ in range(10):
        received = make_received(teacher)
        received.student.areas_of_interest.add(area)
    many = count_queries()

    assert many == few


def test_an_empty_list_is_a_valid_answer(client):
    teacher = make_teacher()

    response = list_requests(client, teacher.user)

    assert response.status_code == 200
    assert response.json() == {"items": [], "count": 0}


def test_students_are_forbidden_and_visitors_are_not_authenticated(client):
    student = make_student()

    as_student = list_requests(client, student.user)
    anonymous = client.get(REQUESTS_URL)

    assert as_student.status_code == 403
    assert anonymous.status_code == 401


def test_an_inactive_teacher_cannot_list(client):
    teacher = make_teacher()
    headers = bearer(teacher.user)
    teacher.user.is_active = False
    teacher.user.save(update_fields=["is_active"])

    assert client.get(REQUESTS_URL, headers=headers).status_code == 401


def test_the_openapi_documents_the_filters_and_the_page_parameters(client):
    schema = client.get("/api/openapi.json").json()

    operation = schema["paths"]["/api/orientacao/solicitacoes"]["get"]
    assert {"status", "limit", "offset"} <= {p["name"] for p in operation["parameters"]}
    assert "200" in operation["responses"]
