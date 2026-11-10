"""The administrator manages courses through the Django Admin (RFADM01-03, RN17, ADR 0010)."""

import pytest
from django.core.management import call_command
from django.db.models.deletion import ProtectedError

from apps.accounts.models import COURSE_WITH_TEACHERS_MESSAGE, Course, Institute, User, UserType
from tests.factories import DEFAULT_PASSWORD, make_course, make_student, make_teacher, make_user

pytestmark = pytest.mark.django_db

COURSES = "/admin/accounts/course/"


def make_admin():
    return User.objects.create_superuser(email="admin@example.com", password=DEFAULT_PASSWORD)


@pytest.fixture
def admin_client(client):
    client.force_login(make_admin())
    return client


def delete_url(course):
    return f"{COURSES}{course.pk}/delete/"


def test_the_administrator_lists_the_courses(admin_client):
    course = make_course()

    response = admin_client.get(COURSES)

    assert response.status_code == 200
    assert course.name in response.content.decode()


def test_the_administrator_creates_a_course_choosing_the_institute(admin_client):
    institute = Institute.objects.create(name="Instituto de Ciências da Educação")

    response = admin_client.post(
        f"{COURSES}add/", {"institute": institute.pk, "name": "Ciência da Computação"}
    )

    assert response.status_code == 302
    course = Course.objects.get(name="Ciência da Computação")
    assert course.institute == institute


def test_the_administrator_also_registers_an_institute_to_support_the_courses(admin_client):
    response = admin_client.post("/admin/accounts/institute/add/", {"name": "Instituto Novo"})

    assert response.status_code == 302
    assert Institute.objects.filter(name="Instituto Novo").exists()


def test_the_administrator_edits_a_course(admin_client):
    course = make_course()

    response = admin_client.post(
        f"{COURSES}{course.pk}/change/", {"institute": course.institute_id, "name": "Nome Novo"}
    )

    assert response.status_code == 302
    course.refresh_from_db()
    assert course.name == "Nome Novo"


def test_the_administrator_deletes_a_course_without_bonds(admin_client):
    course = make_course()

    response = admin_client.post(delete_url(course), {"post": "yes"})

    assert response.status_code == 302
    assert not Course.objects.filter(pk=course.pk).exists()


def assert_the_admin_refuses_to_delete(admin_client, course):
    page = admin_client.get(delete_url(course))
    assert page.status_code == 200
    assert page.context["protected"]
    response = admin_client.post(delete_url(course), {"post": "yes"})
    assert response.status_code == 200
    assert Course.objects.filter(pk=course.pk).exists()


def test_a_course_with_a_student_cannot_be_deleted(admin_client):
    student = make_student()

    assert_the_admin_refuses_to_delete(admin_client, student.course)


def test_a_course_with_a_teacher_cannot_be_deleted_and_keeps_the_link(admin_client):
    course = make_course()
    teacher = make_teacher(courses=[course])

    assert_the_admin_refuses_to_delete(admin_client, course)

    assert list(teacher.courses.all()) == [course]


def test_the_bulk_delete_of_the_admin_also_refuses_a_course_in_use(admin_client):
    in_use = make_course()
    make_teacher(courses=[in_use])
    free = make_course()

    response = admin_client.post(
        COURSES,
        {
            "action": "delete_selected",
            "_selected_action": [in_use.pk, free.pk],
            "post": "yes",
        },
    )

    assert response.status_code == 200
    assert Course.objects.filter(pk__in=[in_use.pk, free.pk]).count() == 2


def test_the_model_refuses_to_delete_a_course_in_use_whoever_calls_it():
    by_teacher = make_course()
    make_teacher(courses=[by_teacher])
    by_student = make_student().course
    free = make_course()

    with pytest.raises(ProtectedError, match=COURSE_WITH_TEACHERS_MESSAGE):
        by_teacher.delete()
    with pytest.raises(ProtectedError):
        by_student.delete()
    with pytest.raises(ProtectedError):
        Course.objects.filter(pk__in=[free.pk, by_teacher.pk]).delete()

    assert Course.objects.filter(pk__in=[free.pk, by_teacher.pk, by_student.pk]).count() == 3
    free.delete()
    assert not Course.objects.filter(pk=free.pk).exists()
    another_free = make_course()
    Course.objects.filter(pk=another_free.pk).delete()
    assert not Course.objects.filter(pk=another_free.pk).exists()


@pytest.mark.parametrize("user_type", [UserType.STUDENT, UserType.TEACHER])
def test_a_student_or_a_teacher_does_not_enter_the_admin(client, user_type):
    user = make_user(user_type)
    client.force_login(user)
    course = make_course()

    pages = [client.get("/admin/"), client.get(COURSES), client.get(f"{COURSES}add/")]
    posts = [
        client.post(f"{COURSES}add/", {"institute": course.institute_id, "name": "Invasão"}),
        client.post(delete_url(course), {"post": "yes"}),
    ]

    assert user.is_staff is False
    for response in pages + posts:
        assert response.status_code == 302
        assert "/admin/login/" in response["Location"]
    assert not Course.objects.filter(name="Invasão").exists()
    assert Course.objects.filter(pk=course.pk).exists()


def test_a_visitor_does_not_enter_the_admin(client):
    response = client.get(COURSES)

    assert response.status_code == 302
    assert "/admin/login/" in response["Location"]


def test_the_administrator_is_created_by_createsuperuser(monkeypatch):
    monkeypatch.setenv("DJANGO_SUPERUSER_PASSWORD", DEFAULT_PASSWORD)

    call_command(
        "createsuperuser",
        interactive=False,
        email="novo-admin@example.com",
        full_name="Nova Administradora",
        type=UserType.ADMIN,
    )

    admin = User.objects.get(email="novo-admin@example.com")
    assert (admin.type, admin.is_staff, admin.is_superuser) == (UserType.ADMIN, True, True)
    assert admin.check_password(DEFAULT_PASSWORD)
