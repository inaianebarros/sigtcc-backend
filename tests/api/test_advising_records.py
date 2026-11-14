"""The records of the report of an ended advising: one paginated feed (RF61, RN47, RN43, RN07)."""

from datetime import UTC, date, datetime

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.deactivation import deactivate_user
from apps.activities.models import ActivityStatus
from apps.meetings.models import MeetingStatus
from tests.api.helpers import bearer
from tests.api.report_builders import add_activity, add_meeting, ended_advising
from tests.factories import make_admin, make_advising, make_student, make_teacher

pytestmark = pytest.mark.django_db

MEETING_FIELDS = {"type", "id", "date", "starts_at", "ends_at", "description", "notes"}
ACTIVITY_FIELDS = {"type", "id", "date", "deadline", "title", "description", "status"}


def records_url(advising_id):
    return f"/api/orientacao/historico/{advising_id}/registros"


def records(client, user, advising_id, **params):
    return client.get(records_url(advising_id), params, headers=bearer(user))


def keys(response):
    return [(item["type"], item["id"]) for item in response.json()["items"]]


def at(year, month, day, hour=15):
    return datetime(year, month, day, hour, 0, tzinfo=UTC)


def test_a_student_receives_a_meeting_and_an_activity_with_the_full_content_of_each(client):
    student = make_student()
    advising = ended_advising(student)
    meeting = add_meeting(
        advising,
        at=at(2025, 11, 10, hour=17),
        description="Discussão dos ajustes finais do trabalho",
        notes="Rever as considerações finais.",
    )
    activity = add_activity(
        advising,
        status=ActivityStatus.ARCHIVED,
        title="Revisar capítulo de resultados",
        description="Atividade entregue pelo orientando",
        deadline=date(2025, 11, 5),
    )

    response = records(client, student.user, advising.id)

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    first, second = body["items"]
    assert set(first) == MEETING_FIELDS
    assert first["type"] == "meeting"
    assert first["id"] == meeting.id
    assert first["date"] == "2025-11-10"
    assert first["description"] == "Discussão dos ajustes finais do trabalho"
    assert first["notes"] == "Rever as considerações finais."
    assert first["starts_at"].startswith("2025-11-10T14:00:00")  # local time, America/Santarem
    assert first["ends_at"].startswith("2025-11-10T15:00:00")
    assert set(second) == ACTIVITY_FIELDS
    assert second == {
        "type": "activity",
        "id": activity.id,
        "date": "2025-11-05",
        "deadline": "2025-11-05",
        "title": "Revisar capítulo de resultados",
        "description": "Atividade entregue pelo orientando",
        "status": "archived",
    }


def test_a_teacher_receives_the_records_of_an_advising_they_ended(client):
    teacher = make_teacher()
    advising = ended_advising(teacher=teacher)
    add_activity(advising)

    assert records(client, teacher.user, advising.id).json()["count"] == 1


def test_the_notes_are_empty_text_when_the_teacher_wrote_none(client):
    student = make_student()
    advising = ended_advising(student)
    add_meeting(advising)

    assert records(client, student.user, advising.id).json()["items"][0]["notes"] == ""


def test_only_the_held_meetings_and_the_concluded_or_archived_activities_are_records(client):
    student = make_student()
    advising = ended_advising(student)
    held_by_clock = add_meeting(advising, days_from_now=-3)
    held_by_mark = add_meeting(advising, days_from_now=2, held=True)
    add_meeting(advising, status=MeetingStatus.PENDING)
    add_meeting(advising, status=MeetingStatus.REJECTED)
    add_meeting(advising, status=MeetingStatus.CANCELLED)
    add_meeting(advising, days_from_now=2)
    completed = add_activity(advising, status=ActivityStatus.COMPLETED)
    archived = add_activity(advising, status=ActivityStatus.ARCHIVED)
    add_activity(advising, status=ActivityStatus.COMPLETED, deleted=True)
    add_activity(advising, status=ActivityStatus.PENDING)
    add_activity(advising, status=ActivityStatus.IN_REVIEW)

    response = records(client, student.user, advising.id)

    assert response.json()["count"] == 4
    assert sorted(keys(response)) == sorted(
        [
            ("meeting", held_by_clock.id),
            ("meeting", held_by_mark.id),
            ("activity", completed.id),
            ("activity", archived.id),
        ]
    )


def test_the_type_filter_returns_only_meetings_only_activities_or_all_and_the_count_follows(client):
    student = make_student()
    advising = ended_advising(student)
    for _ in range(3):
        add_meeting(advising)
    for _ in range(2):
        add_activity(advising)

    meetings = records(client, student.user, advising.id, type="meeting").json()
    activities = records(client, student.user, advising.id, type="activity").json()
    everything = records(client, student.user, advising.id).json()

    assert (meetings["count"], activities["count"], everything["count"]) == (3, 2, 5)
    assert {item["type"] for item in meetings["items"]} == {"meeting"}
    assert {item["type"] for item in activities["items"]} == {"activity"}


def test_an_unknown_type_is_rejected_with_422(client):
    student = make_student()
    advising = ended_advising(student)

    assert records(client, student.user, advising.id, type="other").status_code == 422


def test_the_records_go_from_the_most_recent_date_to_the_oldest_mixing_both_types(client):
    student = make_student()
    advising = ended_advising(student)
    old_activity = add_activity(advising, deadline=date(2025, 3, 1))
    new_meeting = add_meeting(advising, at=at(2025, 11, 10))
    middle_activity = add_activity(advising, deadline=date(2025, 6, 15))
    middle_meeting = add_meeting(advising, at=at(2025, 6, 20))

    response = records(client, student.user, advising.id)

    assert keys(response) == [
        ("meeting", new_meeting.id),
        ("meeting", middle_meeting.id),
        ("activity", middle_activity.id),
        ("activity", old_activity.id),
    ]


def test_records_on_the_same_day_are_ordered_by_type_then_by_the_higher_id_first(client):
    student = make_student()
    advising = ended_advising(student)
    first_meeting = add_meeting(advising, at=at(2025, 5, 5))
    second_meeting = add_meeting(advising, at=at(2025, 5, 5))
    first_activity = add_activity(advising, deadline=date(2025, 5, 5))
    second_activity = add_activity(advising, deadline=date(2025, 5, 5))

    response = records(client, student.user, advising.id)

    assert keys(response) == [
        ("activity", second_activity.id),
        ("activity", first_activity.id),
        ("meeting", second_meeting.id),
        ("meeting", first_meeting.id),
    ]


def test_the_date_of_a_meeting_is_the_day_in_the_institution_time_zone(client):
    student = make_student()
    advising = ended_advising(student)
    add_meeting(advising, at=at(2025, 11, 10, hour=2))  # 23:00 of the 9th in America/Santarem

    assert records(client, student.user, advising.id).json()["items"][0]["date"] == "2025-11-09"


def test_the_feed_is_paginated_with_10_per_page_by_default_in_the_same_order(client):
    student = make_student()
    advising = ended_advising(student)
    for day in range(1, 13):
        add_activity(advising, deadline=date(2025, 1, day))

    first = records(client, student.user, advising.id).json()
    second = records(client, student.user, advising.id, offset=10).json()
    small = records(client, student.user, advising.id, limit=5, offset=5).json()

    assert first["count"] == second["count"] == 12
    assert [item["date"] for item in first["items"]] == [
        f"2025-01-{d:02d}" for d in range(12, 2, -1)
    ]
    assert [item["date"] for item in second["items"]] == ["2025-01-02", "2025-01-01"]
    assert [item["date"] for item in small["items"]] == [
        f"2025-01-{d:02d}" for d in range(7, 2, -1)
    ]


def test_the_page_size_has_a_maximum_of_100(client):
    student = make_student()
    advising = ended_advising(student)

    assert records(client, student.user, advising.id, limit=100).status_code == 200
    assert records(client, student.user, advising.id, limit=101).status_code == 422


def test_an_advising_without_records_has_an_empty_feed(client):
    student = make_student()
    advising = ended_advising(student)

    assert records(client, student.user, advising.id).json() == {"items": [], "count": 0}


def test_the_feed_has_only_the_records_of_this_advising(client):
    student, teacher = make_student(), make_teacher()
    mine = ended_advising(student, teacher)
    other = ended_advising(make_student(), teacher)
    add_activity(mine, title="Minha")
    add_activity(other, title="De outro aluno")
    add_meeting(other)

    items = records(client, student.user, mine.id).json()["items"]

    assert [item["title"] for item in items] == ["Minha"]


def test_the_feed_of_another_users_advising_is_a_404_like_an_unknown_id(client):
    advising = ended_advising()
    add_activity(advising)
    stranger = make_student()

    other = records(client, stranger.user, advising.id)
    unknown = records(client, stranger.user, advising.id + 1000)

    assert other.status_code == unknown.status_code == 404
    assert other.json() == unknown.json()
    assert records(client, make_teacher().user, advising.id).status_code == 404


def test_the_feed_of_an_active_advising_is_a_404_for_both_parties(client):
    student, teacher = make_student(), make_teacher()
    advising = make_advising(student=student, teacher=teacher)

    assert records(client, student.user, advising.id).status_code == 404
    assert records(client, teacher.user, advising.id).status_code == 404


def test_a_visitor_is_unauthorized_and_the_administrator_is_forbidden(client):
    advising = ended_advising()

    assert client.get(records_url(advising.id)).status_code == 401
    assert records(client, make_admin(), advising.id).status_code == 403


@pytest.mark.parametrize("deactivated", ["student", "teacher"])
def test_an_advising_ended_by_the_system_has_a_feed_for_the_other_party(client, deactivated):
    student, teacher = make_student(), make_teacher()
    advising = make_advising(student=student, teacher=teacher)
    add_activity(advising, status=ActivityStatus.COMPLETED)
    deactivate_user(student.user if deactivated == "student" else teacher.user)
    remaining = teacher.user if deactivated == "student" else student.user

    response = records(client, remaining, advising.id)

    assert response.status_code == 200
    assert response.json()["count"] == 1


def test_the_number_of_queries_does_not_grow_with_the_number_of_records(client):
    student = make_student()
    advising = ended_advising(student)
    add_meeting(advising)
    add_activity(advising)
    with CaptureQueriesContext(connection) as few:
        records(client, student.user, advising.id)

    for _ in range(8):
        add_meeting(advising)
        add_activity(advising)
    with CaptureQueriesContext(connection) as many:
        records(client, student.user, advising.id)

    assert len(many) == len(few)


def test_the_openapi_documents_the_feed(client):
    schema = client.get("/api/openapi.json").json()

    operation = schema["paths"]["/api/orientacao/historico/{advising_id}/registros"]["get"]
    names = {parameter["name"] for parameter in operation["parameters"]}
    assert {"type", "limit", "offset"} <= names
    assert operation["description"]
    assert {"401", "403", "404", "422"} <= set(operation["responses"])
    schemas = schema["components"]["schemas"]
    assert set(schemas["MeetingRecordOut"]["properties"]) == MEETING_FIELDS
    assert set(schemas["ActivityRecordOut"]["properties"]) == ACTIVITY_FIELDS
