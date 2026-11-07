import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import UserType
from apps.advising.models import Advising, AdvisingStatus
from tests.api.helpers import ME_URL, bearer
from tests.factories import make_advising, make_student, make_teacher, make_user

pytestmark = pytest.mark.django_db


def read_me(client, user):
    return client.get(ME_URL, headers=bearer(user))


def end(advising):
    Advising.objects.filter(pk=advising.pk).update(
        status=AdvisingStatus.ENDED, ended_at=timezone.now(), end_reason="Encerrada no teste."
    )


def test_a_student_without_an_advising_is_told_so_and_gets_no_teacher_field(client):
    student = make_student()

    body = read_me(client, student.user).json()

    assert body["has_active_advising"] is False
    assert "active_advisees_count" not in body


def test_a_student_with_an_active_advising_is_told_so(client):
    student = make_student()
    make_advising(student=student)

    assert read_me(client, student.user).json()["has_active_advising"] is True


def test_an_ended_advising_does_not_count_for_the_student(client):
    student = make_student()
    end(make_advising(student=student))

    assert read_me(client, student.user).json()["has_active_advising"] is False


def test_a_teacher_gets_the_number_of_active_advisees_and_no_student_field(client):
    teacher = make_teacher()
    assert read_me(client, teacher.user).json()["active_advisees_count"] == 0

    make_advising(teacher=teacher)
    make_advising(teacher=teacher)
    end(make_advising(teacher=teacher))
    make_advising(teacher=make_teacher())

    body = read_me(client, teacher.user).json()

    assert body["active_advisees_count"] == 2
    assert "has_active_advising" not in body


def test_an_administrator_gets_neither_field(client):
    admin = make_user(UserType.ADMIN)

    body = read_me(client, admin).json()

    assert set(body) == {"id", "full_name", "email", "type"}


def test_the_other_session_fields_are_unchanged(client):
    student = make_student()

    body = read_me(client, student.user).json()

    assert body["id"] == student.user_id
    assert body["email"] == student.user.email
    assert body["type"] == "student"
    assert body["full_name"] == student.user.full_name


def test_the_login_response_keeps_the_four_user_fields(browser):
    student = make_student()

    response = browser.post(
        "/api/auth/login",
        data={"email": student.user.email, "password": "senha-longa-123"},
        content_type="application/json",
    )

    assert set(response.json()["user"]) == {"id", "full_name", "email", "type"}


def count_queries(client, user):
    headers = bearer(user)
    with CaptureQueriesContext(connection) as queries:
        assert client.get(ME_URL, headers=headers).status_code == 200
    return len(queries)


def test_the_number_of_queries_does_not_grow_with_the_number_of_advisees(client):
    teacher = make_teacher()
    make_advising(teacher=teacher)
    few = count_queries(client, teacher.user)

    for _ in range(8):
        make_advising(teacher=teacher)
    many = count_queries(client, teacher.user)

    assert many == few


def test_the_openapi_documents_the_optional_fields(client):
    schema = client.get("/api/openapi.json").json()

    properties = schema["components"]["schemas"]["SessionUserDetailOut"]["properties"]

    assert {"has_active_advising", "active_advisees_count"} <= set(properties)
    assert schema["paths"]["/api/auth/me"]["get"]["description"]


def test_the_number_of_queries_does_not_grow_for_a_student_with_more_advisings(client):
    student = make_student()
    end(make_advising(student=student))
    few = count_queries(client, student.user)

    for _ in range(3):
        end(make_advising(student=student))
    make_advising(student=student)
    many = count_queries(client, student.user)

    assert many == few
