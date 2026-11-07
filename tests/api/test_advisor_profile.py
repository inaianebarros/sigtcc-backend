import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import PublishedArticle, UserType
from tests.api.helpers import bearer
from tests.factories import (
    make_advising,
    make_advising_request,
    make_area,
    make_course,
    make_student,
    make_teacher,
    make_user,
)

pytestmark = pytest.mark.django_db

SEARCH_URL = "/api/orientadores/busca"


def profile_url(teacher_id):
    return f"/api/professores/{teacher_id}/perfil"


def read_profile(client, user, teacher_id):
    return client.get(profile_url(teacher_id), headers=bearer(user))


def test_a_student_reads_the_profile_of_a_teacher_of_their_course(client):
    course = make_course()
    other_course_of_the_teacher = make_course()
    student = make_student(course=course)
    teacher = make_teacher(
        courses=[course, other_course_of_the_teacher], full_name="Ana Souza", is_available=False
    )

    response = read_profile(client, student.user, teacher.user_id)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == teacher.user_id
    assert body["full_name"] == "Ana Souza"
    assert body["institute"] == {"id": course.institute_id, "name": course.institute.name}
    assert body["is_available"] is False
    assert sorted(body["courses"], key=lambda c: c["id"]) == sorted(
        [
            {"id": course.id, "name": course.name},
            {"id": other_course_of_the_teacher.id, "name": other_course_of_the_teacher.name},
        ],
        key=lambda c: c["id"],
    )


def test_areas_mark_the_common_ones_and_compatibility_counts_them(client):
    course = make_course()
    shared_b, shared_a = make_area(), make_area()
    only_student, only_teacher = make_area(), make_area()
    student = make_student(course=course, areas=[shared_a, shared_b, only_student])
    teacher = make_teacher(courses=[course], areas=[only_teacher, shared_b, shared_a])

    body = read_profile(client, student.user, teacher.user_id).json()

    assert body["compatibility"] == 2
    assert body["areas"] == sorted(
        [
            {"id": shared_a.id, "name": shared_a.name, "is_common": True},
            {"id": shared_b.id, "name": shared_b.name, "is_common": True},
            {"id": only_teacher.id, "name": only_teacher.name, "is_common": False},
        ],
        key=lambda area: area["name"],
    )


def test_the_profile_agrees_with_the_search_for_the_same_teacher(client):
    course = make_course()
    areas = [make_area() for _ in range(4)]
    student = make_student(course=course, areas=areas[:3])
    teacher = make_teacher(courses=[course], areas=areas[1:])
    make_teacher(courses=[course], areas=areas[:1])

    listed = next(
        item
        for item in client.get(SEARCH_URL, headers=bearer(student.user)).json()["items"]
        if item["id"] == teacher.user_id
    )
    body = read_profile(client, student.user, teacher.user_id).json()

    assert body["compatibility"] == listed["compatibility"] == 2
    assert {area["id"] for area in body["areas"] if area["is_common"]} == {
        area["id"] for area in listed["common_areas"]
    }
    assert body["institute"] == listed["institute"]
    assert body["is_available"] == listed["is_available"]


def test_a_teacher_without_common_areas_has_zero_compatibility(client):
    course = make_course()
    student = make_student(course=course, areas=[make_area()])
    teacher = make_teacher(courses=[course], areas=[make_area()])

    body = read_profile(client, student.user, teacher.user_id).json()

    assert body["compatibility"] == 0
    assert [area["is_common"] for area in body["areas"]] == [False]


def test_articles_come_from_the_most_recent_year_to_the_oldest_then_by_id(client):
    course = make_course()
    student = make_student(course=course)
    teacher = make_teacher(courses=[course])
    other_teacher = make_teacher(courses=[course])
    PublishedArticle.objects.create(teacher=teacher, title="Antigo", year=2020, url="")
    PublishedArticle.objects.create(
        teacher=teacher, title="Recente A", year=2024, url="https://example.com/a"
    )
    PublishedArticle.objects.create(teacher=teacher, title="Recente B", year=2024, url="")
    PublishedArticle.objects.create(teacher=other_teacher, title="De outro", year=2025, url="")
    PublishedArticle.objects.create(student=student, title="Do aluno", year=2025, url="")

    body = read_profile(client, student.user, teacher.user_id).json()

    assert body["articles"] == [
        {"title": "Recente A", "year": 2024, "url": "https://example.com/a"},
        {"title": "Recente B", "year": 2024, "url": ""},
        {"title": "Antigo", "year": 2020, "url": ""},
    ]


def test_a_teacher_without_articles_has_an_empty_list(client):
    course = make_course()
    student = make_student(course=course)
    teacher = make_teacher(courses=[course])

    assert read_profile(client, student.user, teacher.user_id).json()["articles"] == []


def test_a_teacher_of_another_course_an_inactive_one_and_an_unknown_id_look_the_same(client):
    course = make_course()
    student = make_student(course=course)
    other_course = make_teacher(courses=[make_course()])
    inactive = make_teacher(courses=[course], is_active=False)
    no_course = make_teacher()
    unknown_id = max(other_course.user_id, inactive.user_id, no_course.user_id) + 1000

    responses = [
        read_profile(client, student.user, teacher_id)
        for teacher_id in (other_course.user_id, inactive.user_id, no_course.user_id, unknown_id)
    ]

    assert [response.status_code for response in responses] == [404, 404, 404, 404]
    assert len({response.content for response in responses}) == 1
    assert responses[0].json() == {"detail": "Professor não encontrado."}


def test_the_user_id_of_a_student_is_not_a_teacher_profile(client):
    course = make_course()
    student = make_student(course=course)
    other_student = make_student(course=course)

    assert read_profile(client, student.user, other_student.user_id).status_code == 404


def test_the_profile_never_exposes_the_email_the_registration_number_or_the_password(client):
    course = make_course()
    student = make_student(course=course)
    teacher = make_teacher(courses=[course], registration_number="P-SECRETA-123")

    response = read_profile(client, student.user, teacher.user_id)

    assert set(response.json()) == {
        "id",
        "full_name",
        "institute",
        "courses",
        "areas",
        "compatibility",
        "is_available",
        "articles",
    }
    content = response.content.decode()
    assert teacher.user.email not in content
    assert "P-SECRETA-123" not in content
    assert "password" not in content


def test_only_students_can_read_it_and_visitors_are_not_authenticated(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    admin = make_user(UserType.ADMIN)

    assert read_profile(client, teacher.user, teacher.user_id).status_code == 403
    assert read_profile(client, admin, teacher.user_id).status_code == 403
    assert client.get(profile_url(teacher.user_id)).status_code == 401


def test_a_student_with_an_active_advising_or_a_pending_request_can_still_read_it(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    advised = make_student(course=course)
    make_advising(student=advised, teacher=teacher)
    waiting = make_student(course=course)
    make_advising_request(student=waiting, teacher=teacher)

    for student in (advised, waiting):
        assert read_profile(client, student.user, teacher.user_id).status_code == 200


def test_the_own_profile_route_of_the_teacher_still_works(client):
    teacher = make_teacher(courses=[make_course()])
    student = make_student()

    own = client.get("/api/professores/me/perfil", headers=bearer(teacher.user))

    assert own.status_code == 200
    assert own.json()["email"] == teacher.user.email
    assert client.get("/api/professores/me/perfil", headers=bearer(student.user)).status_code == 403


def count_queries(client, user, teacher_id):
    headers = bearer(user)
    with CaptureQueriesContext(connection) as queries:
        response = client.get(profile_url(teacher_id), headers=headers)
    assert response.status_code == 200
    return len(queries)


def test_the_number_of_queries_does_not_grow_with_areas_courses_or_articles(client):
    course = make_course()
    student = make_student(course=course, areas=[make_area(), make_area()])
    teacher = make_teacher(courses=[course], areas=[make_area()])
    PublishedArticle.objects.create(teacher=teacher, title="Primeiro", year=2024, url="")
    few = count_queries(client, student.user, teacher.user_id)

    teacher.courses.add(*[make_course() for _ in range(4)])
    teacher.areas_of_interest.add(
        *student.areas_of_interest.all(), *[make_area() for _ in range(5)]
    )
    for number in range(8):
        PublishedArticle.objects.create(
            teacher=teacher, title=f"Artigo {number}", year=2000 + number, url=""
        )
    many = count_queries(client, student.user, teacher.user_id)

    assert many == few


def test_the_openapi_documents_the_profile_and_its_errors(client):
    schema = client.get("/api/openapi.json").json()

    operation = schema["paths"]["/api/professores/{teacher_id}/perfil"]["get"]
    assert {"200", "404"} <= set(operation["responses"])
    assert operation["parameters"][0]["schema"]["type"] == "integer"
    assert {"institute", "courses", "areas", "compatibility", "is_available", "articles"} <= set(
        schema["components"]["schemas"]["AdvisorProfileOut"]["properties"]
    )


@pytest.mark.parametrize("huge_id", [2**63, 2**63 - 1, 99999999999999999999])
def test_an_id_beyond_the_integer_range_is_just_an_unknown_teacher(client, huge_id):
    student = make_student()

    response = read_profile(client, student.user, huge_id)

    assert response.status_code == 404
    assert response.json() == {"detail": "Professor não encontrado."}
