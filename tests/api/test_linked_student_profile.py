import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import PublishedArticle, UserType
from apps.advising.models import Advising, AdvisingRequest, AdvisingRequestStatus, AdvisingStatus
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

User = get_user_model()


def profile_url(student_id):
    return f"/api/alunos/{student_id}/perfil"


def read_profile(client, user, student_id):
    return client.get(profile_url(student_id), headers=bearer(user))


@pytest.mark.parametrize("status", list(AdvisingRequestStatus))
def test_a_teacher_reads_the_profile_of_a_student_who_sent_them_a_request(client, status):
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    request = make_advising_request(student=student, teacher=teacher)
    AdvisingRequest.objects.filter(pk=request.pk).update(status=status)

    response = read_profile(client, teacher.user, student.user_id)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == student.user_id
    assert body["full_name"] == student.user.full_name
    assert body["institute"] == {"id": course.institute_id, "name": course.institute.name}
    assert body["course"] == {"id": course.id, "name": course.name}


def test_a_teacher_reads_the_profile_of_a_student_with_an_active_or_an_ended_advising(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    advised = make_student(course=course)
    make_advising(student=advised, teacher=teacher)
    graduated = make_student(course=course)
    ended = make_advising(student=graduated, teacher=teacher)
    Advising.objects.filter(pk=ended.pk).update(
        status=AdvisingStatus.ENDED, ended_at=timezone.now()
    )

    for student in (advised, graduated):
        response = read_profile(client, teacher.user, student.user_id)
        assert response.status_code == 200
        assert response.json()["id"] == student.user_id


def test_areas_mark_the_common_ones_and_compatibility_counts_them(client):
    course = make_course()
    shared_b, shared_a = make_area(), make_area()
    only_student, only_teacher = make_area(), make_area()
    teacher = make_teacher(courses=[course], areas=[shared_a, shared_b, only_teacher])
    student = make_student(course=course, areas=[only_student, shared_b, shared_a])
    make_advising_request(student=student, teacher=teacher)

    body = read_profile(client, teacher.user, student.user_id).json()

    assert body["compatibility"] == 2
    assert body["areas"] == sorted(
        [
            {"id": shared_a.id, "name": shared_a.name, "is_common": True},
            {"id": shared_b.id, "name": shared_b.name, "is_common": True},
            {"id": only_student.id, "name": only_student.name, "is_common": False},
        ],
        key=lambda area: area["name"],
    )


def test_the_compatibility_is_the_same_count_the_student_sees_in_the_teachers_profile(client):
    course = make_course()
    areas = [make_area() for _ in range(4)]
    teacher = make_teacher(courses=[course], areas=areas[:3])
    student = make_student(course=course, areas=areas[1:])
    make_advising_request(student=student, teacher=teacher)

    from_the_teacher = read_profile(client, teacher.user, student.user_id).json()
    from_the_student = client.get(
        f"/api/professores/{teacher.user_id}/perfil", headers=bearer(student.user)
    ).json()

    assert from_the_teacher["compatibility"] == from_the_student["compatibility"] == 2


def test_articles_come_from_the_most_recent_year_to_the_oldest_then_by_id(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    other_student = make_student(course=course)
    make_advising_request(student=student, teacher=teacher)
    PublishedArticle.objects.create(student=student, title="Antigo", year=2020, url="")
    PublishedArticle.objects.create(
        student=student, title="Recente A", year=2024, url="https://example.com/a"
    )
    PublishedArticle.objects.create(student=student, title="Recente B", year=2024, url="")
    PublishedArticle.objects.create(student=other_student, title="De outro", year=2025, url="")
    PublishedArticle.objects.create(teacher=teacher, title="Do professor", year=2025, url="")

    body = read_profile(client, teacher.user, student.user_id).json()

    assert body["articles"] == [
        {"title": "Recente A", "year": 2024, "url": "https://example.com/a"},
        {"title": "Recente B", "year": 2024, "url": ""},
        {"title": "Antigo", "year": 2020, "url": ""},
    ]


def test_a_student_without_articles_has_an_empty_list(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    make_advising_request(student=student, teacher=teacher)

    assert read_profile(client, teacher.user, student.user_id).json()["articles"] == []


def test_students_without_a_link_inactive_or_unknown_all_look_the_same(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    other_teacher = make_teacher(courses=[course])
    unlinked = make_student(course=course)
    only_other_request = make_student(course=course)
    make_advising_request(student=only_other_request, teacher=other_teacher)
    only_other_advising = make_student(course=course)
    make_advising(student=only_other_advising, teacher=other_teacher)
    inactive = make_student(course=course)
    make_advising_request(student=inactive, teacher=teacher)
    User.objects.filter(pk=inactive.user_id).update(is_active=False)
    unknown_id = max(inactive.user_id, unlinked.user_id, only_other_advising.user_id) + 1000

    responses = [
        read_profile(client, teacher.user, student_id)
        for student_id in (
            unlinked.user_id,
            only_other_request.user_id,
            only_other_advising.user_id,
            inactive.user_id,
            other_teacher.user_id,
            unknown_id,
        )
    ]

    assert {response.status_code for response in responses} == {404}
    assert len({response.content for response in responses}) == 1
    assert responses[0].json() == {"detail": "Aluno não encontrado."}


@pytest.mark.parametrize("huge_id", [2**63, 2**63 - 1, 99999999999999999999])
def test_an_id_beyond_the_integer_range_is_just_an_unknown_student(client, huge_id):
    teacher = make_teacher()

    response = read_profile(client, teacher.user, huge_id)

    assert response.status_code == 404
    assert response.json() == {"detail": "Aluno não encontrado."}


def test_the_profile_never_exposes_the_email_the_registration_number_or_the_password(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course, registration_number="A-SECRETA-123")
    make_advising_request(student=student, teacher=teacher)

    response = read_profile(client, teacher.user, student.user_id)

    assert set(response.json()) == {
        "id",
        "full_name",
        "institute",
        "course",
        "areas",
        "compatibility",
        "articles",
    }
    content = response.content.decode()
    assert student.user.email not in content
    assert "A-SECRETA-123" not in content
    assert "password" not in content


def test_only_teachers_can_read_it_and_visitors_are_not_authenticated(client):
    course = make_course()
    student = make_student(course=course)
    admin = make_user(UserType.ADMIN)

    assert read_profile(client, student.user, student.user_id).status_code == 403
    assert read_profile(client, admin, student.user_id).status_code == 403
    assert client.get(profile_url(student.user_id)).status_code == 401


def test_the_own_profile_route_of_the_student_still_works(client):
    student = make_student()
    teacher = make_teacher()

    own = client.get("/api/alunos/me/perfil", headers=bearer(student.user))

    assert own.status_code == 200
    assert own.json()["email"] == student.user.email
    assert client.get("/api/alunos/me/perfil", headers=bearer(teacher.user)).status_code == 403


def count_queries(client, user, student_id):
    headers = bearer(user)
    with CaptureQueriesContext(connection) as queries:
        response = client.get(profile_url(student_id), headers=headers)
    assert response.status_code == 200
    return len(queries)


def test_the_number_of_queries_does_not_grow_with_areas_or_articles(client):
    course = make_course()
    teacher = make_teacher(courses=[course], areas=[make_area()])
    student = make_student(course=course, areas=[make_area()])
    make_advising_request(student=student, teacher=teacher)
    PublishedArticle.objects.create(student=student, title="Primeiro", year=2024, url="")
    few = count_queries(client, teacher.user, student.user_id)

    extra_areas = [make_area() for _ in range(5)]
    student.areas_of_interest.add(*extra_areas)
    teacher.areas_of_interest.add(*extra_areas[:3])
    for number in range(8):
        PublishedArticle.objects.create(
            student=student, title=f"Artigo {number}", year=2000 + number, url=""
        )
    many = count_queries(client, teacher.user, student.user_id)

    assert many == few


def test_the_openapi_documents_the_profile_and_its_errors(client):
    schema = client.get("/api/openapi.json").json()

    operation = schema["paths"]["/api/alunos/{student_id}/perfil"]["get"]
    assert {"200", "401", "403", "404"} <= set(operation["responses"])
    assert operation["parameters"][0]["schema"]["type"] == "integer"
    assert operation["description"]
    assert {"institute", "course", "areas", "compatibility", "articles"} <= set(
        schema["components"]["schemas"]["LinkedStudentProfileOut"]["properties"]
    )
