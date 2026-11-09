"""The teacher dashboard: one read-only summary of the active advisings (RF51, RN35–RN38)."""

import datetime

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import UserType
from apps.activities.models import Activity, ActivityStatus
from apps.advising.models import ThesisTopic
from apps.advising.services import close_advising
from tests.api.helpers import bearer
from tests.factories import make_advising, make_course, make_student, make_teacher, make_user

pytestmark = pytest.mark.django_db

URL = "/api/painel/professor"


def get(client, user):
    return client.get(URL, headers=bearer(user))


def in_days(days):
    return timezone.localdate() + datetime.timedelta(days=days)


def teacher_with_course():
    course = make_course()
    return make_teacher(courses=[course], full_name="Carlos Lima"), course


def advisee(teacher, course, name):
    student = make_student(course=course, full_name=name)
    return make_advising(student=student, teacher=teacher)


def add(advising, status=ActivityStatus.PENDING, days=7, title="Atividade", deleted=False):
    return Activity.objects.create(
        advising=advising,
        title=title,
        description="Descrição",
        deadline=in_days(days),
        status=status,
        deleted_at=timezone.now() if deleted else None,
    )


def add_many(advising, count, status):
    Activity.objects.bulk_create(
        Activity(
            advising=advising,
            title=f"A{index}",
            description="d",
            deadline=in_days(30),
            status=status,
        )
        for index in range(count)
    )


def test_the_numbers_add_up_the_active_advisings_of_the_teacher(client):
    teacher, course = teacher_with_course()
    first = advisee(teacher, course, "Ana")
    second = advisee(teacher, course, "Bruno")
    add(first, ActivityStatus.PENDING, days=3)
    add(first, ActivityStatus.PENDING, days=-2)
    add(first, ActivityStatus.COMPLETED, days=-10)
    add(second, ActivityStatus.IN_PROGRESS, days=5)
    add(second, ActivityStatus.IN_REVIEW, days=9)
    add(second, ActivityStatus.COMPLETED, days=1)

    response = get(client, teacher.user)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"advisees_count", "activities", "attention_advisees", "next_deadlines"}
    assert body["advisees_count"] == 2
    assert body["activities"] == {
        "total": 6,
        "pending": 2,
        "in_progress": 1,
        "in_review": 1,
        "completed": 2,
        "overdue": 1,
        "progress_percent": 33,
    }


def test_ended_advisings_archived_deleted_and_other_teachers_activities_do_not_count(client):
    teacher, course = teacher_with_course()
    active = advisee(teacher, course, "Ana")
    ended = advisee(teacher, course, "Bruno")
    add(ended, ActivityStatus.COMPLETED)
    add(ended, ActivityStatus.PENDING, days=-4)
    close_advising(ended, "Concluímos o ciclo de orientação.")
    other_teacher = make_teacher(courses=[course])
    add(advisee(other_teacher, course, "De outro"), ActivityStatus.COMPLETED)
    add(active, ActivityStatus.PENDING)
    add(active, ActivityStatus.ARCHIVED)
    add(active, ActivityStatus.COMPLETED, deleted=True)
    add(active, ActivityStatus.PENDING, days=-3, deleted=True)

    body = get(client, teacher.user).json()

    assert body["advisees_count"] == 1
    assert body["activities"]["total"] == 1
    assert body["activities"]["completed"] == 0
    assert body["activities"]["overdue"] == 0
    assert [item["student"]["full_name"] for item in body["next_deadlines"]["items"]] == ["Ana"]


@pytest.mark.parametrize("with_ended_advising", [False, True])
def test_a_teacher_without_active_advisings_gets_an_empty_response(client, with_ended_advising):
    teacher, course = teacher_with_course()
    if with_ended_advising:
        ended = advisee(teacher, course, "Ana")
        add(ended, ActivityStatus.PENDING, days=-3)
        close_advising(ended, "Concluímos o ciclo de orientação.")

    response = get(client, teacher.user)

    assert response.status_code == 200
    assert response.json() == {
        "advisees_count": 0,
        "activities": None,
        "attention_advisees": None,
        "next_deadlines": None,
    }


def test_an_active_advising_without_activities_has_no_percentage_and_empty_lists(client):
    teacher, course = teacher_with_course()
    advisee(teacher, course, "Ana")

    body = get(client, teacher.user).json()

    assert body["advisees_count"] == 1
    assert body["activities"]["total"] == 0
    assert body["activities"]["progress_percent"] is None
    assert body["attention_advisees"] == {"items": [], "has_more": False}
    assert body["next_deadlines"] == {"items": [], "has_more": False}


def test_the_progress_is_truncated_over_all_the_advisings(client):
    teacher, course = teacher_with_course()
    first = advisee(teacher, course, "Ana")
    second = advisee(teacher, course, "Bruno")
    add_many(first, 100, ActivityStatus.COMPLETED)
    add_many(second, 99, ActivityStatus.COMPLETED)
    add_many(second, 1, ActivityStatus.PENDING)

    body = get(client, teacher.user).json()

    assert body["activities"]["total"] == 200
    assert body["activities"]["progress_percent"] == 99


def test_attention_orders_by_late_then_in_review_then_name_and_skips_who_has_neither(client):
    teacher, course = teacher_with_course()
    quiet = advisee(teacher, course, "Quieto")
    add(quiet, ActivityStatus.PENDING, days=3)
    add(quiet, ActivityStatus.COMPLETED, days=-3)
    two_late = advisee(teacher, course, "Zilda")
    add(two_late, ActivityStatus.PENDING, days=-1)
    add(two_late, ActivityStatus.IN_PROGRESS, days=-2)
    one_late_two_review = advisee(teacher, course, "Marcos")
    add(one_late_two_review, ActivityStatus.PENDING, days=-1)
    add(one_late_two_review, ActivityStatus.IN_REVIEW, days=4)
    add(one_late_two_review, ActivityStatus.IN_REVIEW, days=6)
    one_late_one_review = advisee(teacher, course, "Bruno")
    add(one_late_one_review, ActivityStatus.PENDING, days=-1)
    add(one_late_one_review, ActivityStatus.IN_REVIEW, days=4)
    only_review = advisee(teacher, course, "Ana")
    add(only_review, ActivityStatus.IN_REVIEW, days=4)

    body = get(client, teacher.user).json()

    assert [
        (item["full_name"], item["overdue_count"], item["in_review_count"])
        for item in body["attention_advisees"]["items"]
    ] == [("Zilda", 2, 0), ("Marcos", 1, 2), ("Bruno", 1, 1), ("Ana", 0, 1)]
    assert body["attention_advisees"]["has_more"] is False


def test_attention_breaks_the_last_tie_by_name_ignoring_case(client):
    teacher, course = teacher_with_course()
    for name in ("carla", "Bruno", "ana"):
        add(advisee(teacher, course, name), ActivityStatus.IN_REVIEW)

    items = get(client, teacher.user).json()["attention_advisees"]["items"]

    assert [item["full_name"] for item in items] == ["ana", "Bruno", "carla"]


def test_an_in_review_activity_past_its_deadline_counts_as_late_and_in_review(client):
    teacher, course = teacher_with_course()
    add(advisee(teacher, course, "Ana"), ActivityStatus.IN_REVIEW, days=-2)

    body = get(client, teacher.user).json()

    item = body["attention_advisees"]["items"][0]
    assert (item["overdue_count"], item["in_review_count"]) == (1, 1)
    assert body["activities"]["overdue"] == 1
    assert body["activities"]["in_review"] == 1


def test_attention_item_has_the_name_the_topic_and_the_counts(client):
    teacher, course = teacher_with_course()
    linked = advisee(teacher, course, "Ana")
    other = advisee(teacher, course, "Bruno")
    ThesisTopic.objects.create(advising=linked, description="Redes neurais")
    add(linked, ActivityStatus.PENDING, days=-1)
    add(other, ActivityStatus.PENDING, days=-1)
    add(other, ActivityStatus.PENDING, days=-2)

    items = get(client, teacher.user).json()["attention_advisees"]["items"]

    by_name = {item["full_name"]: item for item in items}
    assert set(by_name["Ana"]) == {
        "id",
        "full_name",
        "thesis_topic",
        "overdue_count",
        "in_review_count",
    }
    assert by_name["Ana"]["id"] == linked.student.user_id
    assert by_name["Ana"]["thesis_topic"] == "Redes neurais"
    assert by_name["Bruno"]["thesis_topic"] is None


def test_attention_is_limited_to_5_and_says_when_there_are_more(client):
    teacher, course = teacher_with_course()
    for index in range(7):
        add(advisee(teacher, course, f"Aluno {index}"), ActivityStatus.PENDING, days=-1)

    attention = get(client, teacher.user).json()["attention_advisees"]

    assert len(attention["items"]) == 5
    assert attention["has_more"] is True


def test_archived_and_deleted_activities_do_not_put_an_advisee_in_attention(client):
    teacher, course = teacher_with_course()
    linked = advisee(teacher, course, "Ana")
    add(linked, ActivityStatus.ARCHIVED, days=-5)
    add(linked, ActivityStatus.PENDING, days=-5, deleted=True)
    add(linked, ActivityStatus.IN_REVIEW, deleted=True)

    body = get(client, teacher.user).json()

    assert body["attention_advisees"]["items"] == []


def test_next_deadlines_are_open_ordered_by_deadline_late_first_and_limited_to_5(client):
    teacher, course = teacher_with_course()
    ana = advisee(teacher, course, "Ana")
    bruno = advisee(teacher, course, "Bruno")
    add(ana, ActivityStatus.PENDING, days=10, title="D10")
    add(bruno, ActivityStatus.IN_REVIEW, days=-4, title="Em revisão atrasada")
    add(ana, ActivityStatus.PENDING, days=2, title="D2")
    add(bruno, ActivityStatus.IN_PROGRESS, days=-1, title="Atrasada ontem")
    add(ana, ActivityStatus.COMPLETED, days=-9, title="Concluída")
    add(bruno, ActivityStatus.PENDING, days=5, title="D5")
    add(ana, ActivityStatus.PENDING, days=30, title="D30")

    deadlines = get(client, teacher.user).json()["next_deadlines"]

    assert [item["title"] for item in deadlines["items"]] == [
        "Em revisão atrasada",
        "Atrasada ontem",
        "D2",
        "D5",
        "D10",
    ]
    assert deadlines["has_more"] is True
    first = deadlines["items"][0]
    assert set(first) == {"id", "title", "student", "deadline", "status", "overdue"}
    assert first["student"] == {"id": bruno.student.user_id, "full_name": "Bruno"}
    assert first["overdue"] is True
    assert first["status"] == "in_review"
    assert deadlines["items"][2]["overdue"] is False


def test_a_deadline_today_is_not_late_and_yesterday_is(client):
    teacher, course = teacher_with_course()
    linked = advisee(teacher, course, "Ana")
    add(linked, ActivityStatus.PENDING, days=0, title="Hoje")
    add(linked, ActivityStatus.PENDING, days=-1, title="Ontem")

    body = get(client, teacher.user).json()

    by_title = {item["title"]: item for item in body["next_deadlines"]["items"]}
    assert by_title["Hoje"]["overdue"] is False
    assert by_title["Ontem"]["overdue"] is True
    assert body["activities"]["overdue"] == 1
    assert [item["full_name"] for item in body["attention_advisees"]["items"]] == ["Ana"]


def test_a_teacher_never_sees_the_data_of_another_teacher(client):
    teacher, course = teacher_with_course()
    mine = advisee(teacher, course, "Minha")
    add(mine, ActivityStatus.PENDING, days=-1, title="Minha atividade")
    other_teacher = make_teacher(courses=[course])
    theirs = advisee(other_teacher, course, "Dele")
    ThesisTopic.objects.create(advising=theirs, description="Tema dele")
    add(theirs, ActivityStatus.IN_REVIEW, days=-5, title="Atividade dele")

    body = get(client, teacher.user).json()

    assert body["advisees_count"] == 1
    assert body["activities"]["total"] == 1
    assert [item["full_name"] for item in body["attention_advisees"]["items"]] == ["Minha"]
    assert [item["title"] for item in body["next_deadlines"]["items"]] == ["Minha atividade"]


def test_anonymous_gets_401_and_a_student_or_an_administrator_gets_403(client):
    student = make_student()
    admin = make_user(UserType.ADMIN)

    assert client.get(URL).status_code == 401
    assert get(client, student.user).status_code == 403
    assert get(client, admin).status_code == 403


def test_the_number_of_queries_does_not_grow_with_the_advisees_or_the_activities(client):
    teacher, course = teacher_with_course()
    first = advisee(teacher, course, "Ana")
    ThesisTopic.objects.create(advising=first, description="Tema")
    add(first, ActivityStatus.PENDING, days=-1)

    def queries_for_a_read():
        with CaptureQueriesContext(connection) as queries:
            assert get(client, teacher.user).status_code == 200
        return len(queries)

    few = queries_for_a_read()
    for index in range(6):
        linked = advisee(teacher, course, f"Aluno {index}")
        ThesisTopic.objects.create(advising=linked, description="Tema")
        for offset in range(5):
            add(linked, list(ActivityStatus)[offset % 4], days=offset - 3)

    assert queries_for_a_read() == few


def test_the_dashboard_is_documented_in_the_openapi_schema(client):
    schema = client.get("/api/openapi.json").json()

    operation = schema["paths"]["/api/painel/professor"]["get"]
    assert set(operation["responses"]) >= {"200", "401", "403"}
    assert operation["summary"]
