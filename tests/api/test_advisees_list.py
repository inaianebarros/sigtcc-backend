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


def advisee_named(teacher, course, name, started_days_ago=0):
    student = make_student(course=course, full_name=name)
    advising = make_advising(student=student, teacher=teacher)
    Advising.objects.filter(pk=advising.pk).update(
        started_at=timezone.now() - timedelta(days=started_days_ago)
    )
    return advising


def names_of(response):
    return [item["student"]["full_name"] for item in response.json()["items"]]


def teacher_with_named_advisees(*names):
    course = make_course()
    teacher = make_teacher(courses=[course])
    for days, name in enumerate(names):
        advisee_named(teacher, course, name, started_days_ago=days)
    return teacher


def test_the_search_filters_by_part_of_the_name_ignoring_case_and_accents(client):
    teacher = teacher_with_named_advisees("José Álvares", "Maria Conceição", "Ana Souza")

    assert names_of(list_advisees(client, teacher.user, search="jose")) == ["José Álvares"]
    assert names_of(list_advisees(client, teacher.user, search="ALVARES")) == ["José Álvares"]
    assert names_of(list_advisees(client, teacher.user, search="conceicao")) == ["Maria Conceição"]
    assert names_of(list_advisees(client, teacher.user, search="é á")) == ["José Álvares"]
    assert names_of(list_advisees(client, teacher.user, search="xyz")) == []
    assert names_of(list_advisees(client, teacher.user, search="a")) == [
        "José Álvares",
        "Maria Conceição",
        "Ana Souza",
    ]


def test_the_search_trims_the_ends_and_an_empty_search_means_no_filter(client):
    teacher = teacher_with_named_advisees("José Álvares", "Ana Souza")

    assert names_of(list_advisees(client, teacher.user, search="  jose  ")) == ["José Álvares"]
    everyone = names_of(list_advisees(client, teacher.user))
    assert names_of(list_advisees(client, teacher.user, search="")) == everyone
    assert names_of(list_advisees(client, teacher.user, search="   ")) == everyone


def test_the_search_only_matches_the_name_of_the_student(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    advising = advisee_named(teacher, course, "Ana Souza")
    ThesisTopic.objects.create(advising=advising, description="Gestão de Orquídeas")

    assert names_of(list_advisees(client, teacher.user, search="orquideas")) == []
    assert (
        names_of(list_advisees(client, teacher.user, search=advising.student.registration_number))
        == []
    )
    assert names_of(list_advisees(client, teacher.user, search=advising.student.user.email)) == []


def test_the_search_ignores_percent_and_underscore_as_wildcards(client):
    teacher = teacher_with_named_advisees("Ana Souza", "Joao_Silva")

    assert names_of(list_advisees(client, teacher.user, search="%")) == []
    assert names_of(list_advisees(client, teacher.user, search="o_s")) == ["Joao_Silva"]


def test_the_search_keeps_the_order_the_pagination_and_the_total_that_reflects_the_filter(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    for number in range(25):
        advisee_named(teacher, course, f"Maria {number:02d}", started_days_ago=number)
    advisee_named(teacher, course, "Pedro Lima", started_days_ago=100)

    first = list_advisees(client, teacher.user, search="maria", limit=10, offset=0)
    last = list_advisees(client, teacher.user, search="maria", limit=10, offset=20)
    other = list_advisees(client, teacher.user, search="pedro")

    assert first.json()["count"] == 25
    assert names_of(first) == [f"Maria {number:02d}" for number in range(10)]
    assert names_of(last) == [f"Maria {number:02d}" for number in range(20, 25)]
    assert other.json()["count"] == 1
    assert list_advisees(client, teacher.user).json()["count"] == 26


def test_the_search_stays_inside_the_scope_of_the_teacher(client):
    course = make_course()
    mine = make_teacher(courses=[course])
    other = make_teacher(courses=[course])
    advisee_named(other, course, "Maria dos Outros")
    ended = advisee_named(mine, course, "Maria Antiga")
    Advising.objects.filter(pk=ended.pk).update(
        status=AdvisingStatus.ENDED, ended_at=timezone.now(), end_reason="Encerrada no teste."
    )
    advisee_named(mine, course, "Maria Atual")

    assert names_of(list_advisees(client, mine.user, search="maria")) == ["Maria Atual"]


def test_the_search_has_at_most_100_characters_and_a_null_byte_matches_nothing(client):
    teacher = teacher_with_named_advisees("Ana Souza")

    assert list_advisees(client, teacher.user, search="a" * 100).status_code == 200
    assert list_advisees(client, teacher.user, search="a" * 101).status_code == 422
    nul = list_advisees(client, teacher.user, search="Ana\x00")
    assert nul.status_code == 200
    assert nul.json() == {"items": [], "count": 0}


def test_the_search_does_not_change_the_shape_of_the_items(client):
    teacher = teacher_with_named_advisees("Ana Souza")

    filtered = list_advisees(client, teacher.user, search="ana").json()["items"][0]
    plain = list_advisees(client, teacher.user).json()["items"][0]

    assert set(filtered) == set(plain)


def test_the_number_of_queries_of_the_search_does_not_grow(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    advisee_named(teacher, course, "Ana Souza")
    headers = bearer(teacher.user)

    def queries():
        with CaptureQueriesContext(connection) as captured:
            assert client.get(ADVISEES_URL, {"search": "ana"}, headers=headers).status_code == 200
        return len(captured)

    few = queries()
    for number in range(8):
        advisee_named(teacher, course, f"Ana {number}")

    assert queries() == few


def test_the_openapi_documents_the_search_parameter(client):
    operation = client.get("/api/openapi.json").json()["paths"][ADVISEES_URL]["get"]

    assert "search" in {parameter["name"] for parameter in operation["parameters"]}


# --- A busca por nome ou tema (parâmetro q), separada da busca por nome (search) ---


def advisee_with_topic(teacher, course, name, topic=None, started_days_ago=0):
    advising = advisee_named(teacher, course, name, started_days_ago=started_days_ago)
    if topic is not None:
        ThesisTopic.objects.create(advising=advising, description=topic)
    return advising


def test_q_filters_by_part_of_the_name_ignoring_case_and_accents(client):
    teacher = teacher_with_named_advisees("José Álvares", "Maria Conceição", "Ana Souza")

    assert names_of(list_advisees(client, teacher.user, q="jose")) == ["José Álvares"]
    assert names_of(list_advisees(client, teacher.user, q="ALVARES")) == ["José Álvares"]
    assert names_of(list_advisees(client, teacher.user, q="conceicao")) == ["Maria Conceição"]
    assert names_of(list_advisees(client, teacher.user, q="xyz")) == []


def test_q_filters_by_part_of_the_topic_ignoring_case_and_accents(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    advisee_with_topic(teacher, course, "Ana Souza", "Gestão de Orquídeas", started_days_ago=1)
    advisee_with_topic(teacher, course, "Bruno Lima", "Banco de dados", started_days_ago=2)

    assert names_of(list_advisees(client, teacher.user, q="orquideas")) == ["Ana Souza"]
    assert names_of(list_advisees(client, teacher.user, q="GESTÃO")) == ["Ana Souza"]
    assert names_of(list_advisees(client, teacher.user, q="banco de")) == ["Bruno Lima"]


def test_q_is_an_or_between_the_name_and_the_topic(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    advisee_with_topic(teacher, course, "Ana Redes", "Banco de dados", started_days_ago=1)
    advisee_with_topic(teacher, course, "Bruno Souza", "Segurança de redes", started_days_ago=2)
    advisee_with_topic(teacher, course, "Carla Dias", "Compiladores", started_days_ago=3)

    assert names_of(list_advisees(client, teacher.user, q="redes")) == ["Ana Redes", "Bruno Souza"]


def test_q_finds_an_advisee_without_topic_by_the_name_and_never_by_a_missing_topic(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    advisee_with_topic(teacher, course, "Ana Sem Tema", None)

    assert names_of(list_advisees(client, teacher.user, q="sem tema")) == ["Ana Sem Tema"]
    assert names_of(list_advisees(client, teacher.user, q="None")) == []


def test_q_trims_the_ends_and_an_empty_q_means_no_filter(client):
    teacher = teacher_with_named_advisees("José Álvares", "Ana Souza")

    assert names_of(list_advisees(client, teacher.user, q="  jose  ")) == ["José Álvares"]
    everyone = names_of(list_advisees(client, teacher.user))
    assert names_of(list_advisees(client, teacher.user, q="")) == everyone
    assert names_of(list_advisees(client, teacher.user, q="   ")) == everyone


def test_q_does_not_match_the_registration_number_or_the_email(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    advising = advisee_with_topic(teacher, course, "Ana Souza", "Redes")

    assert (
        names_of(list_advisees(client, teacher.user, q=advising.student.registration_number)) == []
    )
    assert names_of(list_advisees(client, teacher.user, q=advising.student.user.email)) == []


def test_q_treats_percent_and_underscore_as_plain_characters(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    advisee_with_topic(teacher, course, "Ana Souza", "Meta 100% de cobertura", started_days_ago=1)
    advisee_with_topic(teacher, course, "Joao_Silva", "Redes", started_days_ago=2)

    assert names_of(list_advisees(client, teacher.user, q="100%")) == ["Ana Souza"]
    assert names_of(list_advisees(client, teacher.user, q="o_s")) == ["Joao_Silva"]
    assert names_of(list_advisees(client, teacher.user, q="_____")) == []


def test_q_keeps_the_order_the_pagination_and_the_total_that_reflects_the_filter(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    for number in range(25):
        advisee_with_topic(teacher, course, f"Aluno {number:02d}", "Redes", number)
    advisee_with_topic(teacher, course, "Pedro Lima", "Compiladores", started_days_ago=100)

    first = list_advisees(client, teacher.user, q="redes", limit=10, offset=0)
    last = list_advisees(client, teacher.user, q="redes", limit=10, offset=20)

    assert first.json()["count"] == 25
    assert names_of(first) == [f"Aluno {number:02d}" for number in range(10)]
    assert names_of(last) == [f"Aluno {number:02d}" for number in range(20, 25)]
    assert list_advisees(client, teacher.user).json()["count"] == 26


def test_q_stays_inside_the_active_advisees_of_the_teacher(client):
    course = make_course()
    mine = make_teacher(courses=[course])
    other = make_teacher(courses=[course])
    advisee_with_topic(other, course, "Maria dos Outros", "Redes")
    ended = advisee_with_topic(mine, course, "Maria Antiga", "Redes")
    Advising.objects.filter(pk=ended.pk).update(
        status=AdvisingStatus.ENDED, ended_at=timezone.now(), end_reason="Encerrada no teste."
    )
    advisee_with_topic(mine, course, "Maria Atual", "Redes")

    assert names_of(list_advisees(client, mine.user, q="redes")) == ["Maria Atual"]


def test_q_has_at_most_200_characters_and_a_null_byte_matches_nothing(client):
    teacher = teacher_with_named_advisees("Ana Souza")

    assert list_advisees(client, teacher.user, q="a" * 200).status_code == 200
    assert list_advisees(client, teacher.user, q="a" * 201).status_code == 422
    nul = list_advisees(client, teacher.user, q="Ana\x00")
    assert nul.status_code == 200
    assert nul.json() == {"items": [], "count": 0}


def test_search_and_q_together_keep_only_the_advisees_that_match_both(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    advisee_with_topic(teacher, course, "Ana Souza", "Redes", started_days_ago=1)
    advisee_with_topic(teacher, course, "Ana Lima", "Compiladores", started_days_ago=2)
    advisee_with_topic(teacher, course, "Bruno Souza", "Redes", started_days_ago=3)

    assert names_of(list_advisees(client, teacher.user, search="ana", q="redes")) == ["Ana Souza"]


def test_search_keeps_matching_only_the_name_and_not_the_topic(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    advisee_with_topic(teacher, course, "Ana Souza", "Gestão de Orquídeas")

    assert names_of(list_advisees(client, teacher.user, search="orquideas")) == []
    assert names_of(list_advisees(client, teacher.user, q="orquideas")) == ["Ana Souza"]


def test_q_does_not_change_the_shape_of_the_items(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    advisee_with_topic(teacher, course, "Ana Souza", "Redes")

    filtered = list_advisees(client, teacher.user, q="ana").json()["items"][0]
    plain = list_advisees(client, teacher.user).json()["items"][0]

    assert set(filtered) == set(plain)


def test_the_number_of_queries_of_the_q_search_does_not_grow(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    advisee_with_topic(teacher, course, "Ana Souza", "Redes")
    headers = bearer(teacher.user)

    def queries():
        with CaptureQueriesContext(connection) as captured:
            assert client.get(ADVISEES_URL, {"q": "redes"}, headers=headers).status_code == 200
        return len(captured)

    few = queries()
    for number in range(8):
        advisee_with_topic(teacher, course, f"Ana {number}", "Redes")

    assert queries() == few


def test_the_openapi_documents_q_next_to_search(client):
    operation = client.get("/api/openapi.json").json()["paths"][ADVISEES_URL]["get"]

    assert {"search", "q"} <= {parameter["name"] for parameter in operation["parameters"]}
    assert "tema" in operation["description"]
