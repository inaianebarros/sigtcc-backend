"""The history of ended advisings of a student or a teacher (RF49, RN12, RN33, RN32)."""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.advising.models import Advising, AdvisingStatus, ThesisTopic
from tests.api.helpers import bearer
from tests.factories import make_admin, make_advising, make_student, make_teacher

pytestmark = pytest.mark.django_db

HISTORY_URL = "/api/orientacao/historico"
REASON = "Concluímos o ciclo de orientação do TCC."


def history(client, user, **params):
    return client.get(HISTORY_URL, params, headers=bearer(user))


def end(advising, days_ago=0, reason=REASON, started_days_ago=None):
    update = {
        "status": AdvisingStatus.ENDED,
        "ended_at": timezone.now() - timedelta(days=days_ago),
        "end_reason": reason,
    }
    if started_days_ago is not None:
        update["started_at"] = timezone.now() - timedelta(days=started_days_ago)
    Advising.objects.filter(pk=advising.pk).update(**update)
    advising.refresh_from_db()
    return advising


def test_a_student_sees_an_ended_advising_with_the_teacher_the_dates_the_reason_and_the_topic(
    client,
):
    teacher = make_teacher(full_name="Paulo Professor")
    student = make_student()
    advising = make_advising(student=student, teacher=teacher)
    ThesisTopic.objects.create(advising=advising, description="Aprendizado de máquina")
    end(advising, started_days_ago=40)

    response = history(client, student.user)

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["items"] == [
        {
            "id": advising.id,
            "counterpart": {"id": teacher.user_id, "full_name": "Paulo Professor"},
            "started_at": body["items"][0]["started_at"],
            "ended_at": body["items"][0]["ended_at"],
            "reason": REASON,
            "thesis_topic": "Aprendizado de máquina",
        }
    ]
    assert body["items"][0]["started_at"] < body["items"][0]["ended_at"]


def test_a_teacher_sees_the_advisings_they_ended_with_the_student(client):
    teacher = make_teacher()
    student = make_student(full_name="Ana Aluna")
    advising = end(make_advising(student=student, teacher=teacher))

    response = history(client, teacher.user)

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["id"] for item in items] == [advising.id]
    assert items[0]["counterpart"] == {"id": student.user_id, "full_name": "Ana Aluna"}
    assert items[0]["reason"] == REASON


def test_the_topic_is_null_when_the_advising_never_had_one(client):
    student = make_student()
    end(make_advising(student=student))

    item = history(client, student.user).json()["items"][0]

    assert item["thesis_topic"] is None


def test_a_student_sees_every_ended_advising_with_each_teacher(client):
    student = make_student()
    first_teacher, second_teacher = make_teacher(), make_teacher()
    first = end(make_advising(student=student, teacher=first_teacher), days_ago=20)
    second = end(make_advising(student=student, teacher=second_teacher), days_ago=5)

    items = history(client, student.user).json()["items"]

    assert [item["id"] for item in items] == [second.id, first.id]
    assert [item["counterpart"]["id"] for item in items] == [
        second_teacher.user_id,
        first_teacher.user_id,
    ]


def test_the_active_advising_is_not_in_the_history(client):
    student = make_student()
    teacher = make_teacher()
    make_advising(student=student, teacher=teacher)

    assert history(client, student.user).json() == {"items": [], "count": 0}
    assert history(client, teacher.user).json() == {"items": [], "count": 0}


def test_a_user_never_receives_the_advisings_of_another_user(client):
    mine_teacher, other_teacher = make_teacher(), make_teacher()
    mine_student = make_student()
    other_student = make_student(full_name="Beatriz Alheia")
    mine = end(make_advising(student=mine_student, teacher=mine_teacher))
    other = end(
        make_advising(
            student=other_student,
            teacher=other_teacher,
        )
    )
    ThesisTopic.objects.create(advising=other, description="Tema de outra pessoa")

    for user in (mine_student.user, mine_teacher.user):
        response = history(client, user)

        assert [item["id"] for item in response.json()["items"]] == [mine.id]
        assert "Beatriz Alheia" not in response.text
        assert "Tema de outra pessoa" not in response.text
    stranger = history(client, make_student().user).json()
    assert stranger == {"items": [], "count": 0}
    other_teacher_items = history(client, other_teacher.user).json()["items"]
    assert [item["id"] for item in other_teacher_items] == [other.id]


def test_the_history_goes_from_the_most_recent_ending_to_the_oldest_then_by_id(client):
    teacher = make_teacher()
    oldest = end(make_advising(teacher=teacher), days_ago=30)
    newest = end(make_advising(teacher=teacher), days_ago=1)
    tied_first = end(make_advising(teacher=teacher), days_ago=10)
    tied_second = end(make_advising(teacher=teacher), days_ago=10)
    Advising.objects.filter(pk=tied_second.pk).update(ended_at=tied_first.ended_at)

    items = history(client, teacher.user).json()["items"]

    assert [item["id"] for item in items] == [
        newest.id,
        tied_second.id,
        tied_first.id,
        oldest.id,
    ]


def test_the_history_is_paginated_in_that_order_over_more_than_one_page(client):
    teacher = make_teacher()
    advisings = [end(make_advising(teacher=teacher), days_ago=day) for day in range(1, 6)]
    expected = [advising.id for advising in advisings]

    first = history(client, teacher.user, limit=2, offset=0).json()
    second = history(client, teacher.user, limit=2, offset=2).json()
    last = history(client, teacher.user, limit=2, offset=4).json()

    assert first["count"] == second["count"] == last["count"] == 5
    assert [item["id"] for item in first["items"]] == expected[0:2]
    assert [item["id"] for item in second["items"]] == expected[2:4]
    assert [item["id"] for item in last["items"]] == expected[4:5]


def test_an_item_has_no_data_beyond_the_ones_of_the_history(client):
    student = make_student()
    end(make_advising(student=student))

    item = history(client, student.user).json()["items"][0]

    assert set(item) == {"id", "counterpart", "started_at", "ended_at", "reason", "thesis_topic"}
    assert set(item["counterpart"]) == {"id", "full_name"}


def test_a_visitor_is_unauthorized(client):
    assert client.get(HISTORY_URL).status_code == 401


def test_the_administrator_receives_403(client):
    assert history(client, make_admin()).status_code == 403


def test_the_number_of_queries_does_not_grow_with_the_number_of_advisings(client):
    student = make_student()
    end(make_advising(student=student), days_ago=1)
    with CaptureQueriesContext(connection) as few:
        history(client, student.user)

    for number in range(2, 12):
        advising = end(make_advising(student=student), days_ago=number)
        ThesisTopic.objects.create(advising=advising, description=f"Tema {number}")
    with CaptureQueriesContext(connection) as many:
        history(client, student.user)

    assert len(many) == len(few)


def test_the_openapi_documents_the_history(client):
    schema = client.get("/api/openapi.json").json()

    operation = schema["paths"]["/api/orientacao/historico"]["get"]
    assert {"limit", "offset"} <= {parameter["name"] for parameter in operation["parameters"]}
    assert operation["description"]
    item = schema["components"]["schemas"]["EndedAdvisingItemOut"]
    assert {"id", "counterpart", "started_at", "ended_at", "reason", "thesis_topic"} <= set(
        item["properties"]
    )
