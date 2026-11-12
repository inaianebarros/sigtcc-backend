import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import UserType
from apps.advising.models import Advising, AdvisingStatus
from tests.api.helpers import bearer
from tests.factories import (
    make_advising,
    make_course,
    make_student,
    make_teacher,
    make_user,
)

pytestmark = pytest.mark.django_db

URL = "/api/orientacao/orientandos/calendario"


def calendar_advisees(client, user, **params):
    return client.get(URL, params, headers=bearer(user))


def end(advising):
    Advising.objects.filter(pk=advising.pk).update(
        status=AdvisingStatus.ENDED, ended_at=timezone.now(), end_reason="Encerrada no teste."
    )


def names(response):
    return [item["full_name"] for item in response.json()["items"]]


def test_the_teacher_lists_students_of_active_and_ended_advisings_marking_the_ended_ones(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    active = make_student(course=course, full_name="Ana Ativa")
    finished = make_student(course=course, full_name="Bruno Encerrado")
    make_advising(student=active, teacher=teacher)
    end(make_advising(student=finished, teacher=teacher))

    response = calendar_advisees(client, teacher.user)

    assert response.status_code == 200
    assert response.json()["count"] == 2
    assert response.json()["items"] == [
        {"id": active.user_id, "full_name": "Ana Ativa", "ended": False},
        {"id": finished.user_id, "full_name": "Bruno Encerrado", "ended": True},
    ]


def test_a_student_with_more_than_one_advising_appears_once(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course, full_name="Carla Repetida")
    for _ in range(2):
        end(make_advising(student=student, teacher=teacher))
    current = make_advising(student=student, teacher=teacher)

    body = calendar_advisees(client, teacher.user).json()

    assert body["count"] == 1
    assert body["items"] == [{"id": student.user_id, "full_name": "Carla Repetida", "ended": False}]
    end(current)
    assert calendar_advisees(client, teacher.user).json()["items"][0]["ended"] is True


def test_students_of_other_teachers_and_without_advising_never_appear(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    other_teacher = make_teacher(courses=[course])
    mine = make_student(course=course, full_name="Dora Minha")
    make_advising(student=mine, teacher=teacher)
    make_advising(
        student=make_student(course=course, full_name="Eva De Outro"), teacher=other_teacher
    )
    make_student(course=course, full_name="Fábio Sem Vínculo")

    assert names(calendar_advisees(client, teacher.user)) == ["Dora Minha"]
    assert names(calendar_advisees(client, other_teacher.user)) == ["Eva De Outro"]


def test_inactive_students_do_not_appear(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    gone = make_student(course=course, full_name="Gil Inativo")
    make_advising(student=gone, teacher=teacher)
    gone.user.is_active = False
    gone.user.save(update_fields=["is_active"])

    body = calendar_advisees(client, teacher.user).json()

    assert body == {"items": [], "count": 0}


def test_the_name_search_runs_on_the_server_ignoring_case_and_accents(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    for full_name in ("José Álvaro", "Conceição Silva", "Maria Souza"):
        make_advising(student=make_student(course=course, full_name=full_name), teacher=teacher)

    assert names(calendar_advisees(client, teacher.user, search="jose alvaro")) == ["José Álvaro"]
    assert names(calendar_advisees(client, teacher.user, search="CONCEICAO")) == ["Conceição Silva"]
    assert names(calendar_advisees(client, teacher.user, search="  souza  ")) == ["Maria Souza"]
    assert names(calendar_advisees(client, teacher.user, search="")) != []
    assert calendar_advisees(client, teacher.user, search="zzz").json() == {"items": [], "count": 0}


def test_the_search_filters_the_count_and_the_pages(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    for number in range(5):
        make_advising(
            student=make_student(course=course, full_name=f"Maria {number}"), teacher=teacher
        )
    make_advising(student=make_student(course=course, full_name="Pedro"), teacher=teacher)

    first = calendar_advisees(client, teacher.user, search="maria", limit=2).json()
    last = calendar_advisees(client, teacher.user, search="maria", limit=2, offset=4).json()

    assert (first["count"], len(first["items"])) == (5, 2)
    assert (last["count"], len(last["items"])) == (5, 1)


def test_the_order_is_by_name_without_accents_then_id_and_stable_across_pages(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    for full_name in ("beto Lima", "Álvaro Dias", "Alzira Reis", "Zélia Souza", "Alzira Reis"):
        make_advising(student=make_student(course=course, full_name=full_name), teacher=teacher)

    everybody = calendar_advisees(client, teacher.user).json()["items"]
    paged = []
    for offset in (0, 2, 4):
        paged += calendar_advisees(client, teacher.user, limit=2, offset=offset).json()["items"]

    assert [item["full_name"] for item in everybody] == [
        "Álvaro Dias",
        "Alzira Reis",
        "Alzira Reis",
        "beto Lima",
        "Zélia Souza",
    ]
    twins = [item["id"] for item in everybody if item["full_name"] == "Alzira Reis"]
    assert twins == sorted(twins)
    assert paged == everybody


def test_the_default_page_is_twenty_and_the_limit_is_bounded(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    for number in range(25):
        make_advising(
            student=make_student(course=course, full_name=f"Aluno {number:02d}"), teacher=teacher
        )

    body = calendar_advisees(client, teacher.user).json()

    assert (body["count"], len(body["items"])) == (25, 20)
    assert calendar_advisees(client, teacher.user, limit=101).status_code == 422


def test_students_and_admins_are_refused_and_visitors_are_not_authenticated(client):
    student = make_student()

    assert calendar_advisees(client, student.user).status_code == 403
    assert calendar_advisees(client, make_user(UserType.ADMIN)).status_code == 403
    assert client.get(URL).status_code == 401


def test_a_name_longer_than_a_full_name_or_with_a_null_byte_is_handled(client):
    teacher = make_teacher(courses=[make_course()])

    assert calendar_advisees(client, teacher.user, search="a" * 101).status_code == 422
    assert calendar_advisees(client, teacher.user, search="a\x00b").json() == {
        "items": [],
        "count": 0,
    }


def test_the_existing_active_advisees_list_is_unchanged(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    active = make_advising(student=make_student(course=course), teacher=teacher)
    end(make_advising(student=make_student(course=course), teacher=teacher))

    body = client.get("/api/orientacao/orientandos", headers=bearer(teacher.user)).json()

    assert [item["advising_id"] for item in body["items"]] == [active.id]


def test_the_number_of_queries_does_not_grow_with_the_number_of_students(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    make_advising(student=make_student(course=course), teacher=teacher)

    def count():
        with CaptureQueriesContext(connection) as queries:
            response = calendar_advisees(client, teacher.user)
        assert response.status_code == 200
        return len(queries)

    few = count()
    for _ in range(15):
        make_advising(student=make_student(course=course), teacher=teacher)

    assert count() == few


def test_the_openapi_documents_the_endpoint_and_its_parameters(client):
    schema = client.get("/api/openapi.json").json()

    operation = schema["paths"]["/api/orientacao/orientandos/calendario"]["get"]
    assert {p["name"] for p in operation["parameters"]} == {"search", "limit", "offset"}
    assert "RF59" in operation["description"]
    assert {"id", "full_name", "ended"} <= set(
        schema["components"]["schemas"]["CalendarAdviseeOut"]["properties"]
    )
