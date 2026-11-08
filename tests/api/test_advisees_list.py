from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import UserType
from apps.activities.models import Activity, ActivityStatus
from apps.advising.models import Advising, AdvisingStatus, ThesisTopic
from tests.api.helpers import bearer
from tests.factories import (
    make_activity,
    make_advising,
    make_course,
    make_student,
    make_teacher,
    make_user,
)

pytestmark = pytest.mark.django_db

ADVISEES_URL = "/api/orientacao/orientandos"


def list_advisees(client, user, **params):
    return client.get(ADVISEES_URL, params, headers=bearer(user))


def test_a_teacher_sees_their_active_advisee_with_student_course_and_start(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    advising = make_advising(student=student, teacher=teacher)

    response = list_advisees(client, teacher.user)

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    item = body["items"][0]
    assert item["advising_id"] == advising.id
    assert item["student"] == {
        "id": student.user_id,
        "full_name": student.user.full_name,
        "course": {"id": course.id, "name": course.name},
    }
    assert item["started_at"] is not None


def advise(teacher, course, started_days_ago=0, ended=False):
    student = make_student(course=course)
    advising = make_advising(student=student, teacher=teacher)
    update = {"started_at": timezone.now() - timedelta(days=started_days_ago)}
    if ended:
        update.update(
            status=AdvisingStatus.ENDED, ended_at=timezone.now(), end_reason="Encerrada no teste."
        )
    Advising.objects.filter(pk=advising.pk).update(**update)
    return advising


def test_ended_advisings_and_other_teachers_advisees_are_not_listed(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    other_teacher = make_teacher(courses=[course])
    mine = advise(teacher, course)
    advise(teacher, course, ended=True)
    advise(other_teacher, course)

    body = list_advisees(client, teacher.user).json()

    assert [item["advising_id"] for item in body["items"]] == [mine.id]
    assert body["count"] == 1


def test_advisees_come_from_the_most_recent_start_to_the_oldest_then_by_id(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    oldest = advise(teacher, course, started_days_ago=30)
    newest = advise(teacher, course, started_days_ago=1)
    middle = advise(teacher, course, started_days_ago=10)
    moment = timezone.now() - timedelta(days=5)
    first_tied = advise(teacher, course)
    second_tied = advise(teacher, course)
    Advising.objects.filter(pk__in=[first_tied.pk, second_tied.pk]).update(started_at=moment)

    ordered = [item["advising_id"] for item in list_advisees(client, teacher.user).json()["items"]]

    assert ordered == [newest.id, second_tied.id, first_tied.id, middle.id, oldest.id]


def test_the_thesis_topic_is_the_text_or_null_when_there_is_none(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    with_topic = advise(teacher, course, started_days_ago=1)
    without_topic = advise(teacher, course, started_days_ago=2)
    ThesisTopic.objects.create(advising=with_topic, description="Gestão de TCC com Django")

    items = {
        item["advising_id"]: item for item in list_advisees(client, teacher.user).json()["items"]
    }

    assert items[with_topic.id]["thesis_topic"] == "Gestão de TCC com Django"
    assert items[without_topic.id]["thesis_topic"] is None


def add_activities(advising, status, how_many, deleted=False):
    for _ in range(how_many):
        activity = make_activity(advising=advising)
        Activity.objects.filter(pk=activity.pk).update(
            status=status, deleted_at=timezone.now() if deleted else None
        )


def test_the_activities_are_counted_by_status_without_the_deleted_ones(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    busy = advise(teacher, course, started_days_ago=1)
    idle = advise(teacher, course, started_days_ago=2)
    add_activities(busy, ActivityStatus.PENDING, 2)
    add_activities(busy, ActivityStatus.IN_PROGRESS, 1)
    add_activities(busy, ActivityStatus.IN_REVIEW, 3)
    add_activities(busy, ActivityStatus.COMPLETED, 4)
    add_activities(busy, ActivityStatus.PENDING, 5, deleted=True)
    add_activities(busy, ActivityStatus.COMPLETED, 1, deleted=True)
    add_activities(advise(make_teacher(courses=[course]), course), ActivityStatus.PENDING, 7)

    items = {
        item["advising_id"]: item for item in list_advisees(client, teacher.user).json()["items"]
    }

    assert items[busy.id]["activities"] == {
        "pending": 2,
        "in_progress": 1,
        "in_review": 3,
        "completed": 4,
    }
    assert items[idle.id]["activities"] == {
        "pending": 0,
        "in_progress": 0,
        "in_review": 0,
        "completed": 0,
    }


def test_the_list_is_paginated_and_an_empty_one_is_just_an_empty_page(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    assert list_advisees(client, teacher.user).json() == {"items": [], "count": 0}
    for number in range(25):
        advise(teacher, course, started_days_ago=number)

    first = list_advisees(client, teacher.user).json()
    second = list_advisees(client, teacher.user, offset=20).json()
    small = list_advisees(client, teacher.user, limit=3, offset=1).json()

    assert (first["count"], len(first["items"])) == (25, 20)
    assert (second["count"], len(second["items"])) == (25, 5)
    assert len(small["items"]) == 3
    assert list_advisees(client, teacher.user, limit=101).status_code == 422


def test_the_item_never_exposes_the_email_the_registration_number_or_the_password(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    advising = advise(teacher, course)
    advising.student.registration_number = "A-SECRETA-123"
    advising.student.save()

    response = list_advisees(client, teacher.user)

    item = response.json()["items"][0]
    assert set(item) == {
        "advising_id",
        "student",
        "started_at",
        "thesis_topic",
        "activities",
    }
    assert set(item["student"]) == {"id", "full_name", "course"}
    content = response.content.decode()
    assert advising.student.user.email not in content
    assert "A-SECRETA-123" not in content
    assert "password" not in content


def test_only_teachers_can_list_and_visitors_are_not_authenticated(client):
    student = make_student()
    admin = make_user(UserType.ADMIN)

    assert list_advisees(client, student.user).status_code == 403
    assert list_advisees(client, admin).status_code == 403
    assert client.get(ADVISEES_URL).status_code == 401


def count_queries(client, user):
    headers = bearer(user)
    with CaptureQueriesContext(connection) as queries:
        response = client.get(ADVISEES_URL, headers=headers)
    assert response.status_code == 200
    return len(queries)


def test_the_number_of_queries_does_not_grow_with_the_number_of_advisees(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    first = advise(teacher, course, started_days_ago=1)
    add_activities(first, ActivityStatus.PENDING, 1)
    few = count_queries(client, teacher.user)

    for number in range(2, 12):
        advising = advise(teacher, course, started_days_ago=number)
        add_activities(advising, ActivityStatus.COMPLETED, 3)
        ThesisTopic.objects.create(advising=advising, description=f"Tema {number}")
    many = count_queries(client, teacher.user)

    assert many == few


def test_the_openapi_documents_the_list(client):
    schema = client.get("/api/openapi.json").json()

    operation = schema["paths"]["/api/orientacao/orientandos"]["get"]
    assert {"limit", "offset"} <= {parameter["name"] for parameter in operation["parameters"]}
    assert operation["description"]
    assert {"advising_id", "student", "started_at", "thesis_topic", "activities"} <= set(
        schema["components"]["schemas"]["AdviseeOut"]["properties"]
    )
