"""The command that creates the demonstration users of every profile (#138)."""

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.accounts.demo_seed import DEMO_DOMAIN, DEMO_PASSWORD
from apps.accounts.models import AreaOfInterest, Course, Institute, Student, Teacher, User, UserType
from tests.api.helpers import LOGIN_URL, bearer, post_json

pytestmark = pytest.mark.django_db

COMMAND = "seed_demo_data"
INSTITUTE = "Instituto de Engenharia e Geociências"
OWN_COURSE = "Ciência da Computação"
OTHER_COURSE = "Sistemas de Informação"
SEARCH_URL = "/api/orientadores/busca"

STUDENTS = [
    "aluno1.sem-orientacao",
    "aluno2.solicitacao-pendente",
    "aluno3.solicitacao-recusada",
    "aluno4.solicitacao-nao-respondida",
    "aluno5.orientacao-completa",
    "aluno6.orientacao-vazia",
    "aluno7.orientacao-encerrada",
    "aluno8.outro-curso",
]
TEACHERS = [
    "prof1.orientandos",
    "prof2.sem-orientandos",
    "prof3.indisponivel",
    "prof4.outro-curso",
]


def email(name):
    return f"{name}@{DEMO_DOMAIN}"


def seed():
    out = StringIO()
    call_command(COMMAND, stdout=out)
    return out.getvalue()


def user_of(name):
    return User.objects.get(email=email(name))


def test_it_creates_the_eight_students_the_four_teachers_and_the_administrator(catalog, debug):
    seed()

    assert Student.objects.count() == 8
    assert Teacher.objects.count() == 4
    for name in STUDENTS:
        assert user_of(name).type == UserType.STUDENT
    for name in TEACHERS:
        assert user_of(name).type == UserType.TEACHER
    assert User.objects.get(email=email("admin")).type == UserType.ADMIN
    assert User.objects.count() == 13


@pytest.mark.parametrize("name", [*STUDENTS, *TEACHERS, "admin"])
def test_every_demonstration_user_signs_in_with_the_single_password(browser, catalog, debug, name):
    seed()

    response = post_json(browser, LOGIN_URL, {"email": email(name), "password": "Senha-Demo-2026"})

    assert response.status_code == 200


def test_running_it_again_creates_nothing_more(catalog, debug):
    seed()
    before = (User.objects.count(), Student.objects.count(), Teacher.objects.count())

    second = seed()

    assert (User.objects.count(), Student.objects.count(), Teacher.objects.count()) == before
    lines = [line for line in second.splitlines() if "@" in line]
    assert len(lines) == 13
    assert all("já existe" in line for line in lines)


def test_it_refuses_to_run_without_debug_and_creates_nothing(catalog, settings):
    settings.DEBUG = False

    with pytest.raises(CommandError, match="DJANGO_DEBUG"):
        call_command(COMMAND, stdout=StringIO())

    assert User.objects.count() == 0


def test_it_asks_for_the_catalog_when_it_is_missing(debug):
    with pytest.raises(CommandError, match="seed.json"):
        call_command(COMMAND, stdout=StringIO())

    assert User.objects.count() == 0


def test_it_adds_only_the_other_course_to_the_existing_institute(catalog, debug):
    seed()
    seed()

    institute = Institute.objects.get(name=INSTITUTE)
    assert Institute.objects.count() == 1
    assert set(Course.objects.values_list("name", flat=True)) == {OWN_COURSE, OTHER_COURSE}
    assert Course.objects.filter(institute=institute).count() == 2
    assert AreaOfInterest.objects.count() == 17


def test_the_courses_follow_the_scenarios(catalog, debug):
    seed()

    for name in STUDENTS[:7]:
        assert user_of(name).student.course.name == OWN_COURSE
    assert user_of("aluno8.outro-curso").student.course.name == OTHER_COURSE
    for name in TEACHERS[:3]:
        assert [course.name for course in user_of(name).teacher.courses.all()] == [OWN_COURSE]
    assert [c.name for c in user_of("prof4.outro-curso").teacher.courses.all()] == [OTHER_COURSE]


def test_every_user_has_areas_and_students_share_areas_with_teachers(catalog, debug):
    seed()

    for student in Student.objects.all():
        assert student.areas_of_interest.count() >= 1
    for teacher in Teacher.objects.all():
        assert teacher.areas_of_interest.count() >= 1
    student_areas = set(user_of("aluno1.sem-orientacao").student.areas_of_interest.all())
    shared = [
        name
        for name in TEACHERS[:3]
        if student_areas & set(user_of(name).teacher.areas_of_interest.all())
    ]
    assert len(shared) >= 2


def test_student_one_finds_the_available_teachers_of_the_own_course_by_compatibility(
    client, catalog, debug
):
    seed()
    student = user_of("aluno1.sem-orientacao")

    response = client.get(SEARCH_URL, headers=bearer(student))

    items = response.json()["items"]
    found = [item["id"] for item in items]
    assert user_of("prof3.indisponivel").id not in found
    assert user_of("prof4.outro-curso").id not in found
    assert set(found) == {user_of("prof1.orientandos").id, user_of("prof2.sem-orientandos").id}
    compatibilities = [item["compatibility"] for item in items]
    assert response.json()["count"] == 2
    assert all(value >= 1 for value in compatibilities)
    assert compatibilities == sorted(compatibilities, reverse=True)
    assert items[0]["id"] == user_of("prof2.sem-orientandos").id


def test_student_eight_does_not_find_the_teachers_of_the_other_course(client, catalog, debug):
    seed()

    response = client.get(SEARCH_URL, headers=bearer(user_of("aluno8.outro-curso")))

    assert [item["id"] for item in response.json()["items"]] == [user_of("prof4.outro-curso").id]


def test_only_the_third_teacher_is_unavailable(catalog, debug):
    seed()

    unavailable = Teacher.objects.filter(is_available=False)
    assert [teacher.user.email for teacher in unavailable] == [email("prof3.indisponivel")]


def test_the_administrator_enters_the_admin(client, catalog, debug):
    seed()
    admin = User.objects.get(email=email("admin"))

    client.force_login(admin)

    assert (admin.type, admin.is_staff, admin.is_superuser) == (UserType.ADMIN, True, True)
    assert client.get("/admin/accounts/user/").status_code == 200


@pytest.mark.parametrize("name", [STUDENTS[0], TEACHERS[0]])
def test_a_student_and_a_teacher_do_not_enter_the_admin(client, catalog, debug, name):
    seed()
    user = user_of(name)

    client.force_login(user)

    assert user.is_staff is False
    assert client.get("/admin/accounts/user/").status_code == 302


def test_a_registration_number_already_taken_is_reported_and_nothing_is_created(catalog, debug):
    course = Course.objects.get(name=OWN_COURSE)
    taken = User.objects.create_user(email="outra@example.com", password="x", full_name="Outra")
    Student.objects.create(user=taken, course=course, registration_number="2024DEMO03")

    with pytest.raises(CommandError, match="Nada foi criado"):
        call_command(COMMAND, stdout=StringIO())

    assert User.objects.count() == 1


def test_it_prints_the_password_and_every_email(catalog, debug):
    output = seed()

    assert DEMO_PASSWORD in output
    for name in [*STUDENTS, *TEACHERS, "admin"]:
        assert email(name) in output


def test_it_names_the_problem_when_an_area_of_the_catalog_is_missing(catalog, debug):
    AreaOfInterest.objects.filter(name="Computação Móvel").delete()

    with pytest.raises(CommandError, match="áreas"):
        call_command(COMMAND, stdout=StringIO())

    assert User.objects.count() == 0
