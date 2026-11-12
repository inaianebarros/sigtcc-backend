import time

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import Teacher, UserType
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

SEARCH_URL = "/api/orientadores/busca"


def search_default(client, user, **params):
    return client.get(SEARCH_URL, params, headers=bearer(user))


def search(client, user, **params):
    """Searches with show_all=true, so the tests that do not care about areas need no common one."""
    return search_default(client, user, **{"show_all": "true", **params})


def names(response):
    return [item["full_name"] for item in response.json()["items"]]


def test_only_active_teachers_of_the_students_own_course_are_listed(client):
    course = make_course()
    student = make_student(course=course)
    own = make_teacher(courses=[course], full_name="Ana Souza")
    make_teacher(courses=[make_course()], full_name="Bruno de Outro Curso")
    make_teacher(courses=[course], full_name="Carla Inativa", is_active=False)
    both = make_teacher(courses=[course, make_course()], full_name="Davi dos Dois Cursos")

    response = search(client, student.user)

    assert response.status_code == 200
    assert response.json()["count"] == 2
    assert {item["id"] for item in response.json()["items"]} == {own.user_id, both.user_id}


def test_the_course_cannot_be_chosen_by_a_parameter(client):
    course = make_course()
    other_course = make_course()
    student = make_student(course=course)
    make_teacher(courses=[other_course], full_name="Bruno de Outro Curso")

    response = search(client, student.user, course_id=other_course.id, course=other_course.id)

    assert response.json() == {"items": [], "count": 0}


def test_teachers_are_ordered_by_common_areas_then_name_then_id(client):
    course = make_course()
    areas = [make_area() for _ in range(4)]
    student = make_student(course=course, areas=areas[:3])
    three = make_teacher(courses=[course], areas=areas[:3], full_name="Zilda Tres")
    two = make_teacher(courses=[course], areas=areas[:2] + [areas[3]], full_name="Yara Duas")
    one_late = make_teacher(courses=[course], areas=[areas[0]], full_name="Wagner Uma")
    one_early = make_teacher(courses=[course], areas=[areas[2], areas[3]], full_name="Ana Uma")
    twin_first = make_teacher(courses=[course], areas=[areas[1]], full_name="Gêmeo Igual")
    twin_second = make_teacher(courses=[course], areas=[areas[1]], full_name="Gêmeo Igual")

    response = search(client, student.user)

    ordered = [item["id"] for item in response.json()["items"]]
    assert ordered == [
        three.user_id,
        two.user_id,
        one_early.user_id,
        twin_first.user_id,
        twin_second.user_id,
        one_late.user_id,
    ]
    assert [item["compatibility"] for item in response.json()["items"]] == [3, 2, 1, 1, 1, 1]


def test_each_item_lists_only_the_common_areas_by_name(client):
    course = make_course()
    shared_b = make_area()
    shared_a = make_area()
    only_teacher = make_area()
    only_student = make_area()
    student = make_student(course=course, areas=[shared_a, shared_b, only_student])
    make_teacher(courses=[course], areas=[shared_b, shared_a, only_teacher])

    item = search(client, student.user).json()["items"][0]

    assert item["compatibility"] == 2
    assert item["common_areas"] == sorted(
        [{"id": shared_a.id, "name": shared_a.name}, {"id": shared_b.id, "name": shared_b.name}],
        key=lambda area: area["name"],
    )


def test_a_teacher_without_common_areas_still_appears_with_zero_compatibility(client):
    course = make_course()
    student = make_student(course=course, areas=[make_area()])
    make_teacher(courses=[course], areas=[make_area()])

    item = search(client, student.user).json()["items"][0]

    assert item["compatibility"] == 0
    assert item["common_areas"] == []


def test_each_item_carries_the_institute_and_the_availability(client):
    course = make_course()
    area = make_area()
    student = make_student(course=course, areas=[area])
    unavailable = make_teacher(
        courses=[course], areas=[area], full_name="Zilda Ocupada", is_available=False
    )
    available = make_teacher(courses=[course], areas=[], full_name="Ana Livre", is_available=True)

    items = search(client, student.user).json()["items"]

    assert [item["id"] for item in items] == [available.user_id, unavailable.user_id]
    assert [item["is_available"] for item in items] == [True, False]
    institute = {"id": course.institute_id, "name": course.institute.name}
    assert [item["institute"] for item in items] == [institute, institute]


def student_and_course():
    course = make_course()
    return make_student(course=course), course


def test_the_name_filter_matches_part_of_the_name_ignoring_case(client):
    student, course = student_and_course()
    make_teacher(courses=[course], full_name="Ana Souza")
    make_teacher(courses=[course], full_name="Bruno Lima")

    assert names(search(client, student.user, name="souza")) == ["Ana Souza"]
    assert names(search(client, student.user, name="LIMA")) == ["Bruno Lima"]
    assert names(search(client, student.user, name="a s")) == ["Ana Souza"]


def test_the_name_filter_ignores_accents_in_both_directions(client):
    student, course = student_and_course()
    make_teacher(courses=[course], full_name="José Álvaro")
    make_teacher(courses=[course], full_name="Conceicao Silva")

    assert names(search(client, student.user, name="jose alvaro")) == ["José Álvaro"]
    assert names(search(client, student.user, name="ÁLVARO")) == ["José Álvaro"]
    assert names(search(client, student.user, name="conceição")) == ["Conceicao Silva"]


def test_wildcard_characters_in_the_name_are_treated_literally(client):
    student, course = student_and_course()
    make_teacher(courses=[course], full_name="Ana 100% Souza")
    make_teacher(courses=[course], full_name="Ana 100X Souza")
    make_teacher(courses=[course], full_name="Bruno_Lima")
    make_teacher(courses=[course], full_name="Bruno Lima")

    assert names(search(client, student.user, name="100%")) == ["Ana 100% Souza"]
    assert names(search(client, student.user, name="o_l")) == ["Bruno_Lima"]
    assert names(search(client, student.user, name="%")) == ["Ana 100% Souza"]


@pytest.mark.parametrize("blank", ["", "   "])
def test_an_empty_name_means_no_filter(client, blank):
    student, course = student_and_course()
    make_teacher(courses=[course], full_name="Ana Souza")
    make_teacher(courses=[course], full_name="Bruno Lima")

    assert search(client, student.user, name=blank).json()["count"] == 2


def test_the_name_is_trimmed_before_filtering(client):
    student, course = student_and_course()
    make_teacher(courses=[course], full_name="Ana Souza")
    make_teacher(courses=[course], full_name="Bruno Lima")

    assert names(search(client, student.user, name="  souza  ")) == ["Ana Souza"]


def test_the_filter_keeps_the_order_and_counts_only_the_matches(client):
    student, course = student_and_course()
    area = make_area()
    student.areas_of_interest.set([area])
    make_teacher(courses=[course], areas=[], full_name="Maria A")
    make_teacher(courses=[course], areas=[area], full_name="Maria B")
    make_teacher(courses=[course], areas=[area], full_name="Pedro")

    response = search(client, student.user, name="maria")

    assert names(response) == ["Maria B", "Maria A"]
    assert response.json()["count"] == 2


def test_the_list_is_paginated_with_twenty_per_page_by_default_and_a_count(client):
    student, course = student_and_course()
    for number in range(25):
        make_teacher(courses=[course], full_name=f"Professor {number:02d}")

    first = search(client, student.user).json()
    second = search(client, student.user, offset=20).json()

    assert first["count"] == 25 and len(first["items"]) == 20
    assert second["count"] == 25 and len(second["items"]) == 5
    assert [item["full_name"] for item in first["items"] + second["items"]] == [
        f"Professor {number:02d}" for number in range(25)
    ]


def test_limit_and_offset_choose_the_page(client):
    student, course = student_and_course()
    for number in range(7):
        make_teacher(courses=[course], full_name=f"Professor {number}")

    page = search(client, student.user, limit=3, offset=2).json()

    assert [item["full_name"] for item in page["items"]] == [
        "Professor 2",
        "Professor 3",
        "Professor 4",
    ]
    assert page["count"] == 7


@pytest.mark.parametrize("params", [{"limit": 101}, {"limit": 0}, {"offset": -1}])
def test_a_limit_above_one_hundred_or_a_negative_page_is_rejected(client, params):
    student, _ = student_and_course()

    assert search(client, student.user, **params).status_code == 422


def test_the_maximum_limit_and_a_huge_offset_are_accepted(client):
    student, course = student_and_course()
    make_teacher(courses=[course])

    assert search(client, student.user, limit=100).status_code == 200
    beyond = search(client, student.user, offset=2**31 - 1)
    assert beyond.status_code == 200
    assert beyond.json() == {"items": [], "count": 1}


def test_only_students_can_search_and_visitors_are_not_authenticated(client):
    teacher = make_teacher()
    admin = make_user(UserType.ADMIN)

    assert search(client, teacher.user).status_code == 403
    assert search(client, admin).status_code == 403
    assert client.get(SEARCH_URL).status_code == 401


def test_a_student_with_an_active_advising_or_a_pending_request_can_still_search(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    advised = make_student(course=course)
    make_advising(student=advised, teacher=teacher)
    waiting = make_student(course=course)
    make_advising_request(student=waiting, teacher=teacher)

    for student in (advised, waiting):
        response = search(client, student.user)
        assert response.status_code == 200
        assert response.json()["count"] == 1


def count_queries(client, user, **params):
    headers = bearer(user)
    with CaptureQueriesContext(connection) as queries:
        response = client.get(SEARCH_URL, params, headers=headers)
    assert response.status_code == 200
    return len(queries)


def test_the_number_of_queries_does_not_grow_with_the_number_of_teachers(client):
    student, course = student_and_course()
    areas = [make_area() for _ in range(3)]
    student.areas_of_interest.set(areas)
    for number in range(3):
        make_teacher(courses=[course], areas=areas, full_name=f"Professor {number:02d}")
    few = count_queries(client, student.user, name="professor")

    for number in range(3, 20):
        make_teacher(courses=[course], areas=areas, full_name=f"Professor {number:02d}")
    many = count_queries(client, student.user, name="professor")

    assert many == few


def seed_teachers(course, areas, total):
    users = User.objects.bulk_create(
        [
            User(
                email=f"bulk{number}@example.com",
                full_name=f"Professor Em Massa {number:05d}",
                type=UserType.TEACHER,
            )
            for number in range(total)
        ]
    )
    teachers = Teacher.objects.bulk_create(
        [Teacher(user=user, registration_number=f"B{user.id:07d}") for user in users]
    )
    Teacher.courses.through.objects.bulk_create(
        [Teacher.courses.through(teacher_id=t.pk, course_id=course.id) for t in teachers]
    )
    Teacher.areas_of_interest.through.objects.bulk_create(
        [
            Teacher.areas_of_interest.through(teacher_id=t.pk, areaofinterest_id=area.id)
            for t in teachers
            for area in areas[: 1 + t.pk % len(areas)]
        ]
    )


def analyze_seeded_tables():
    """Refreshes the planner statistics of the tables just filled.

    After the tests that truncate the tables the statistics say they are empty, and the planner may
    pick a plan that is slow for thousands of rows, which made this test fail only in the full run.
    """
    tables = [
        User._meta.db_table,
        Teacher._meta.db_table,
        Teacher.courses.through._meta.db_table,
        Teacher.areas_of_interest.through._meta.db_table,
    ]
    with connection.cursor() as cursor:
        cursor.execute(f"ANALYZE {', '.join(tables)}")


def test_a_few_thousand_teachers_are_searched_within_three_seconds(client):
    student, course = student_and_course()
    areas = [make_area() for _ in range(5)]
    student.areas_of_interest.set(areas)
    seed_teachers(course, areas, total=3000)
    analyze_seeded_tables()
    headers = bearer(student.user)

    started = time.perf_counter()
    plain = client.get(SEARCH_URL, headers=headers)
    filtered = client.get(SEARCH_URL, {"name": "em massa 0299"}, headers=headers)
    elapsed = time.perf_counter() - started

    assert plain.json()["count"] == 3000
    assert filtered.json()["count"] == 10
    assert elapsed < 3


def test_the_openapi_documents_the_search_and_its_parameters(client):
    schema = client.get("/api/openapi.json").json()

    operation = schema["paths"]["/api/orientadores/busca"]["get"]
    assert {parameter["name"] for parameter in operation["parameters"]} == {
        "name",
        "show_all",
        "limit",
        "offset",
    }
    assert operation["description"]
    assert {"institute", "compatibility", "common_areas", "is_available"} <= set(
        schema["components"]["schemas"]["AdvisorOut"]["properties"]
    )


def test_a_nul_character_in_the_name_is_not_a_server_error(client):
    student, course = student_and_course()
    make_teacher(courses=[course], full_name="Ana Souza")

    response = search(client, student.user, name="a\x00b")

    assert response.status_code == 200
    assert response.json() == {"items": [], "count": 0}


def test_a_name_longer_than_a_full_name_can_be_is_rejected(client):
    student, _ = student_and_course()

    assert search(client, student.user, name="a" * 151).status_code == 422


def test_the_alphabetical_tie_break_ignores_accents_and_case(client):
    student, course = student_and_course()
    for full_name in ("beto Lima", "Álvaro Dias", "Alzira Reis", "Zélia Souza"):
        make_teacher(courses=[course], full_name=full_name)

    assert names(search(client, student.user)) == [
        "Álvaro Dias",
        "Alzira Reis",
        "beto Lima",
        "Zélia Souza",
    ]


def show_all_flag(client):
    schema = client.get("/api/openapi.json").json()
    operation = schema["paths"]["/api/orientadores/busca"]["get"]
    return next(p for p in operation["parameters"] if p["name"] == "show_all")


def course_student_and_teachers():
    """Aluno com as áreas a, b e c; Davi e Eva não têm área em comum e Zilda e Eva estão indisponíveis."""
    course = make_course()
    areas = [make_area() for _ in range(3)]
    student = make_student(course=course, areas=areas)
    return student, {
        "ana": make_teacher(courses=[course], areas=areas[:1], full_name="Ana Livre"),
        "zilda": make_teacher(
            courses=[course], areas=areas, full_name="Zilda Ocupada", is_available=False
        ),
        "bruno": make_teacher(courses=[course], areas=areas[:2], full_name="Bruno Livre"),
        "davi": make_teacher(courses=[course], areas=[make_area()], full_name="Davi Sem Area"),
        "eva": make_teacher(
            courses=[course], areas=[make_area()], full_name="Eva Sem Area", is_available=False
        ),
    }


def ids(response):
    return [item["id"] for item in response.json()["items"]]


def test_by_default_only_teachers_with_a_common_area_are_listed_available_or_not(client):
    student, t = course_student_and_teachers()

    response = search_default(client, student.user)

    assert response.json()["count"] == 3
    assert ids(response) == [t["bruno"].user_id, t["ana"].user_id, t["zilda"].user_id]
    assert all(item["compatibility"] >= 1 for item in response.json()["items"])


def test_show_all_lists_everybody_in_the_course_with_availability_first(client):
    student, t = course_student_and_teachers()

    response = search_default(client, student.user, show_all="true")

    assert response.json()["count"] == 5
    assert ids(response) == [
        t["bruno"].user_id,
        t["ana"].user_id,
        t["davi"].user_id,
        t["zilda"].user_id,
        t["eva"].user_id,
    ]


def test_a_teacher_who_becomes_unavailable_moves_after_the_available_ones(client):
    student, t = course_student_and_teachers()
    ana = t["ana"]
    ana.is_available = False
    ana.save(update_fields=["is_available"])

    assert ids(search_default(client, student.user)) == [
        t["bruno"].user_id,
        t["zilda"].user_id,
        ana.user_id,
    ]


def test_the_name_filter_combines_with_the_area_cut(client):
    course = make_course()
    area = make_area()
    student = make_student(course=course, areas=[area])
    with_area = make_teacher(courses=[course], areas=[area], full_name="Maria Com Area")
    make_teacher(courses=[course], areas=[], full_name="Maria Sem Area")
    make_teacher(courses=[course], areas=[area], full_name="Pedro Com Area")

    default = search_default(client, student.user, name="maria").json()
    everybody = search_default(client, student.user, name="maria", show_all="true").json()

    assert [item["id"] for item in default["items"]] == [with_area.user_id]
    assert default["count"] == 1
    assert [item["full_name"] for item in everybody["items"]] == [
        "Maria Com Area",
        "Maria Sem Area",
    ]
    assert everybody["count"] == 2


def test_the_count_and_the_pages_follow_the_area_cut_and_stay_stable(client):
    course = make_course()
    area = make_area()
    student = make_student(course=course, areas=[area])
    for number in range(5):
        make_teacher(courses=[course], areas=[area], full_name=f"Compatível {number}")
    for number in range(3):
        make_teacher(courses=[course], areas=[], full_name=f"Incompatível {number}")

    first = search_default(client, student.user, limit=3).json()
    last = search_default(client, student.user, limit=3, offset=3).json()
    everybody = search_default(client, student.user, limit=4, offset=4, show_all="true").json()

    assert (first["count"], len(first["items"])) == (5, 3)
    assert (last["count"], len(last["items"])) == (5, 2)
    assert {i["full_name"] for i in first["items"] + last["items"]} == {
        f"Compatível {n}" for n in range(5)
    }
    assert everybody["count"] == 8
    assert [i["full_name"] for i in everybody["items"]][1:] == [
        f"Incompatível {n}" for n in range(3)
    ]


@pytest.mark.parametrize(
    ("spelling", "lists_everybody"),
    [("true", True), ("1", True), ("false", False), ("0", False), ("TRUE", True)],
)
def test_the_accepted_spellings_of_show_all(client, spelling, lists_everybody):
    student, course = student_and_course()
    make_teacher(courses=[course], full_name="Sem Area")

    count = search_default(client, student.user, show_all=spelling).json()["count"]

    assert count == (1 if lists_everybody else 0)


def test_an_invalid_value_for_show_all_is_rejected(client):
    student, _ = student_and_course()

    assert search_default(client, student.user, show_all="talvez").status_code == 422


def test_only_available_no_longer_exists_and_is_ignored(client):
    student, t = course_student_and_teachers()

    assert ids(search_default(client, student.user, only_available="false")) == ids(
        search_default(client, student.user)
    )


def test_the_openapi_documents_show_all_and_its_default(client):
    parameter = show_all_flag(client)

    assert parameter["schema"]["type"] == "boolean"
    assert parameter["schema"]["default"] is False
    assert parameter["description"]


def test_the_profile_of_a_teacher_without_common_area_stays_accessible(client):
    student, t = course_student_and_teachers()

    response = client.get(
        f"/api/professores/{t['davi'].user_id}/perfil", headers=bearer(student.user)
    )

    assert response.status_code == 200


def test_other_courses_and_inactive_teachers_never_appear_in_either_mode(client):
    course = make_course()
    area = make_area()
    student = make_student(course=course, areas=[area])
    own = make_teacher(courses=[course], areas=[area], full_name="Ana Do Curso")
    make_teacher(courses=[make_course()], areas=[area], full_name="Bruno De Fora")
    make_teacher(courses=[course], areas=[area], full_name="Carla Inativa", is_active=False)

    assert ids(search_default(client, student.user)) == [own.user_id]
    assert ids(search_default(client, student.user, show_all="true")) == [own.user_id]
