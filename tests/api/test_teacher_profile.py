import pytest

from apps.accounts.models import PublishedArticle
from apps.advising.models import AdvisingRequestStatus, AdvisingStatus
from tests.api.helpers import bearer, errors_by_field
from tests.factories import (
    make_advising,
    make_advising_request,
    make_area,
    make_course,
    make_student,
    make_teacher,
)

pytestmark = pytest.mark.django_db

PROFILE_URL = "/api/professores/me/perfil"


def make_teacher_with_courses(*courses, **kwargs):
    teacher = make_teacher(**kwargs)
    teacher.courses.set(courses or [make_course()])
    return teacher


def patch(client, user, payload):
    return client.patch(
        PROFILE_URL, data=payload, content_type="application/json", headers=bearer(user)
    )


def test_a_teacher_reads_their_own_profile(client):
    course = make_course()
    teacher = make_teacher_with_courses(course, registration_number="P2024")
    area = make_area()
    teacher.areas_of_interest.add(area)
    teacher.is_available = False
    teacher.save(update_fields=["is_available"])
    PublishedArticle.objects.create(
        teacher=teacher, title="Grafos em redes", year=2024, url="https://example.com/grafos"
    )

    response = client.get(PROFILE_URL, headers=bearer(teacher.user))

    assert response.status_code == 200
    assert response.json() == {
        "full_name": teacher.user.full_name,
        "email": teacher.user.email,
        "registration_number": "P2024",
        "institute_id": course.institute_id,
        "course_ids": [course.id],
        "area_ids": [area.id],
        "is_available": False,
        "articles": [
            {"title": "Grafos em redes", "year": 2024, "url": "https://example.com/grafos"}
        ],
    }


def article(**overrides):
    return {"title": "Um artigo", "year": 2024, "url": "https://example.com/a", **overrides}


def test_a_teacher_changes_only_the_name_and_it_shows_in_the_session(client):
    teacher = make_teacher_with_courses()
    area = make_area()
    teacher.areas_of_interest.add(area)

    response = patch(client, teacher.user, {"full_name": "  Prof. Carlos Lima "})

    assert response.status_code == 200
    body = response.json()
    assert body["full_name"] == "Prof. Carlos Lima"
    assert body["area_ids"] == [area.id]
    assert body["is_available"] is True
    me = client.get("/api/auth/me", headers=bearer(teacher.user))
    assert me.json()["full_name"] == "Prof. Carlos Lima"


def test_the_teacher_controls_their_own_availability(client):
    teacher = make_teacher_with_courses()

    off = patch(client, teacher.user, {"is_available": False})
    on = patch(client, teacher.user, {"is_available": True})

    assert off.status_code == 200
    assert off.json()["is_available"] is False
    assert on.json()["is_available"] is True


def test_areas_and_articles_sent_replace_the_whole_lists(client):
    teacher = make_teacher_with_courses()
    kept, dropped, added = make_area(), make_area(), make_area()
    teacher.areas_of_interest.add(kept, dropped)
    old = PublishedArticle.objects.create(teacher=teacher, title="Antigo", year=2020)

    response = patch(
        client,
        teacher.user,
        {
            "area_ids": [kept.id, added.id],
            "articles": [article(title="Antigo", year=2020, url="https://new.example.com")],
        },
    )

    assert response.status_code == 200
    assert response.json()["area_ids"] == sorted([kept.id, added.id])
    old.refresh_from_db()
    assert old.url == "https://new.example.com"
    assert teacher.articles.count() == 1


@pytest.mark.parametrize(
    ("payload", "field", "message"),
    [
        ({"area_ids": []}, "area_ids", "Escolha ao menos uma área de interesse."),
        ({"area_ids": [999999]}, "area_ids", "Área de interesse não encontrada."),
        ({"full_name": "   "}, "full_name", "Informe o nome completo."),
        (
            {"articles": [article(), article(title=" UM  artigo ")]},
            "articles",
            "Há artigos repetidos: informe cada artigo (título e ano) uma só vez.",
        ),
    ],
)
def test_invalid_changes_are_rejected_and_nothing_changes(client, payload, field, message):
    teacher = make_teacher_with_courses()
    area = make_area()
    teacher.areas_of_interest.add(area)
    original_name = teacher.user.full_name

    response = patch(client, teacher.user, {"full_name": "Outro Nome", **payload})

    assert response.status_code == 422
    assert errors_by_field(response)[field] == message
    teacher.user.refresh_from_db()
    assert teacher.user.full_name == original_name
    assert list(teacher.areas_of_interest.values_list("id", flat=True)) == [area.id]


def test_the_courses_sent_replace_the_whole_list(client):
    first = make_course()
    second = make_course(first.institute)
    third = make_course(first.institute)
    teacher = make_teacher_with_courses(first, second)

    response = patch(client, teacher.user, {"course_ids": [second.id, third.id]})

    assert response.status_code == 200
    assert response.json()["course_ids"] == sorted([second.id, third.id])
    assert set(teacher.courses.values_list("id", flat=True)) == {second.id, third.id}


@pytest.mark.parametrize(
    ("make_ids", "message"),
    [
        (lambda own, other: [], "Escolha ao menos um curso."),
        (lambda own, other: [999999], "Curso não encontrado."),
        (
            lambda own, other: [own.id, other.id],
            "Todos os cursos devem pertencer ao seu instituto.",
        ),
    ],
)
def test_the_courses_must_exist_belong_to_the_institute_and_not_be_empty(client, make_ids, message):
    own = make_course()
    other = make_course()
    teacher = make_teacher_with_courses(own)

    response = patch(client, teacher.user, {"course_ids": make_ids(own, other)})

    assert response.status_code == 422
    assert errors_by_field(response) == {"course_ids": message}
    assert list(teacher.courses.values_list("id", flat=True)) == [own.id]


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("full_name", "Informe o nome completo."),
        ("course_ids", "Escolha ao menos um curso."),
        ("area_ids", "Escolha ao menos uma área de interesse."),
        ("is_available", "Informe se você está disponível para orientar."),
        ("articles", "Envie a lista de artigos; use uma lista vazia para removê-los todos."),
    ],
)
def test_an_explicit_null_is_rejected_and_nothing_is_removed(client, field, message):
    teacher = make_teacher_with_courses()
    teacher.areas_of_interest.add(make_area())
    PublishedArticle.objects.create(teacher=teacher, title="Antigo", year=2020)

    response = patch(client, teacher.user, {field: None})

    assert response.status_code == 422
    assert errors_by_field(response) == {field: message}
    assert teacher.articles.count() == 1
    assert teacher.areas_of_interest.count() == 1
    assert teacher.courses.count() == 1


@pytest.mark.parametrize("field", ["email", "registration_number", "institute_id", "password"])
def test_read_only_fields_are_rejected(client, field):
    teacher = make_teacher_with_courses()

    response = patch(client, teacher.user, {"full_name": "Outro", field: "x"})

    assert response.status_code == 422
    assert errors_by_field(response) == {field: "Este campo não pode ser alterado."}


def test_an_unknown_field_is_rejected_and_course_id_is_not_a_teacher_field(client):
    teacher = make_teacher_with_courses()

    response = patch(client, teacher.user, {"apelido": "x", "course_id": 1})

    assert response.status_code == 422
    assert errors_by_field(response) == {
        "apelido": "Campo não reconhecido.",
        "course_id": "Campo não reconhecido.",
    }


def test_changing_courses_or_availability_does_not_touch_advisings_or_pending_requests(client):
    course = make_course()
    other_course = make_course(course.institute)
    teacher = make_teacher_with_courses(course)
    advising = make_advising(student=make_student(course=course), teacher=teacher)
    pending = make_advising_request(student=make_student(course=course), teacher=teacher)

    response = patch(client, teacher.user, {"course_ids": [other_course.id], "is_available": False})

    assert response.status_code == 200
    advising.refresh_from_db()
    pending.refresh_from_db()
    assert advising.status == AdvisingStatus.ACTIVE
    assert pending.status == AdvisingRequestStatus.PENDING


@pytest.mark.parametrize("method", ["get", "patch"])
def test_an_anonymous_visitor_is_not_authenticated(client, method):
    response = getattr(client, method)(PROFILE_URL, content_type="application/json")

    assert response.status_code == 401


@pytest.mark.parametrize("method", ["get", "patch"])
def test_a_student_cannot_use_the_teacher_profile(client, method):
    student = make_student()

    response = getattr(client, method)(
        PROFILE_URL, data={}, content_type="application/json", headers=bearer(student.user)
    )

    assert response.status_code == 403


def test_the_openapi_lists_only_the_editable_fields(client):
    schema = client.get("/api/openapi.json").json()

    properties = schema["components"]["schemas"]["TeacherProfileIn"]["properties"]
    assert set(properties) == {"full_name", "area_ids", "articles", "course_ids", "is_available"}


def test_a_teacher_without_courses_can_still_read_and_recover_the_profile(client):
    teacher = make_teacher()
    course = make_course()
    sibling = make_course(course.institute)

    read = client.get(PROFILE_URL, headers=bearer(teacher.user))
    fixed = patch(client, teacher.user, {"course_ids": [course.id, sibling.id]})

    assert read.status_code == 200
    assert read.json()["institute_id"] is None
    assert read.json()["course_ids"] == []
    assert fixed.status_code == 200
    assert fixed.json()["institute_id"] == course.institute_id


def test_a_teacher_without_courses_must_still_pick_courses_from_a_single_institute(client):
    teacher = make_teacher()

    response = patch(client, teacher.user, {"course_ids": [make_course().id, make_course().id]})

    assert response.status_code == 422
    assert errors_by_field(response) == {
        "course_ids": "Todos os cursos devem pertencer ao seu instituto."
    }
    assert not teacher.courses.exists()


@pytest.mark.parametrize("value", ["true", "yes", 1, 0, "abc"])
def test_availability_must_be_a_real_boolean(client, value):
    teacher = make_teacher_with_courses()

    response = patch(client, teacher.user, {"is_available": value})

    assert response.status_code == 422
    assert "is_available" in errors_by_field(response)
    teacher.refresh_from_db()
    assert teacher.is_available is True


def test_an_empty_body_changes_nothing(client):
    teacher = make_teacher_with_courses()
    PublishedArticle.objects.create(teacher=teacher, title="Antigo", year=2020)

    response = patch(client, teacher.user, {})

    assert response.status_code == 200
    assert [a["title"] for a in response.json()["articles"]] == ["Antigo"]


def test_repeated_course_ids_are_treated_as_one(client):
    first = make_course()
    second = make_course(first.institute)
    teacher = make_teacher_with_courses(first)

    response = patch(client, teacher.user, {"course_ids": [second.id, second.id]})

    assert response.status_code == 200
    assert response.json()["course_ids"] == [second.id]


def test_an_inactive_teacher_cannot_use_the_profile(client):
    teacher = make_teacher_with_courses()
    headers = bearer(teacher.user)
    teacher.user.is_active = False
    teacher.user.save(update_fields=["is_active"])

    response = client.get(PROFILE_URL, headers=headers)

    assert response.status_code == 401
