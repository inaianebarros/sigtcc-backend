import pytest

from apps.accounts.models import Course, Institute, Teacher, UserType
from tests.api.helpers import PASSWORD, assert_rejected_on, errors_by_field, post_json
from tests.factories import make_area, make_student, make_teacher

pytestmark = pytest.mark.django_db

REGISTER_URL = "/api/auth/register/professor"


def valid_payload(**overrides):
    institute = Institute.objects.create(name="Instituto de Engenharia e Geociências")
    courses = [
        Course.objects.create(institute=institute, name="Ciência da Computação"),
        Course.objects.create(institute=institute, name="Sistemas de Informação"),
    ]
    payload = {
        "registration_number": "P2024001",
        "full_name": "Beatriz Lima",
        "email": "beatriz.lima@example.com",
        "password": PASSWORD,
        "password_confirmation": PASSWORD,
        "institute_id": institute.id,
        "course_ids": [course.id for course in courses],
        "area_ids": [make_area().id, make_area().id],
    }
    payload.update(overrides)
    return payload


def register(client, payload):
    return post_json(client, REGISTER_URL, payload)


def test_a_visitor_registers_as_a_teacher_with_several_courses(client):
    payload = valid_payload()

    response = register(client, payload)

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "beatriz.lima@example.com"
    assert body["type"] == "teacher"
    assert body["registration_number"] == "P2024001"
    assert body["is_available"] is True
    assert body["institute_id"] == payload["institute_id"]
    assert sorted(body["course_ids"]) == sorted(payload["course_ids"])
    assert sorted(body["area_ids"]) == sorted(payload["area_ids"])
    assert "password" not in body and "password_confirmation" not in body

    teacher = Teacher.objects.get(user__email="beatriz.lima@example.com")
    assert teacher.user.type == UserType.TEACHER
    assert teacher.is_available is True
    assert set(teacher.courses.values_list("id", flat=True)) == set(payload["course_ids"])
    assert set(teacher.areas_of_interest.values_list("id", flat=True)) == set(payload["area_ids"])
    assert teacher.user.check_password(PASSWORD)
    assert teacher.user.password != PASSWORD


def test_a_teacher_may_teach_in_a_single_course(client):
    payload = valid_payload()
    payload["course_ids"] = payload["course_ids"][:1]

    assert register(client, payload).status_code == 201


def test_email_is_unique_across_account_types_ignoring_case(client):
    student = make_student()

    payload = valid_payload(email=student.user.email.upper())

    assert_rejected_on(client, REGISTER_URL, payload, "email")


def test_password_confirmation_must_match(client):
    payload = valid_payload(password_confirmation="Outra-Senha-2026")

    assert_rejected_on(client, REGISTER_URL, payload, "password_confirmation")


@pytest.mark.parametrize("weak_password", ["curta1", "12345678", "password"])
def test_weak_password_is_rejected(client, weak_password):
    payload = valid_payload(password=weak_password, password_confirmation=weak_password)

    assert_rejected_on(client, REGISTER_URL, payload, "password")


def test_email_already_used_by_another_teacher_is_rejected_ignoring_case(client):
    teacher = make_teacher()

    assert_rejected_on(
        client, REGISTER_URL, valid_payload(email=teacher.user.email.upper()), "email"
    )


def test_invalid_email_is_rejected(client):
    assert_rejected_on(client, REGISTER_URL, valid_payload(email="isto-nao-e-um-email"), "email")


@pytest.mark.parametrize("field", ["registration_number", "full_name"])
def test_blank_text_fields_are_rejected(client, field):
    assert_rejected_on(client, REGISTER_URL, valid_payload(**{field: "   "}), field)


def test_surrounding_whitespace_is_trimmed_before_saving(client):
    payload = valid_payload(full_name="  Beatriz Lima  ", registration_number=" P2024001 ")

    body = register(client, payload).json()

    assert body["full_name"] == "Beatriz Lima"
    assert body["registration_number"] == "P2024001"


def test_registration_number_must_be_unique_among_teachers(client):
    make_teacher(registration_number="P2024001")

    assert_rejected_on(client, REGISTER_URL, valid_payload(), "registration_number")


def test_registration_number_may_match_a_students(client):
    make_student(registration_number="P2024001")

    assert register(client, valid_payload()).status_code == 201


def test_at_least_one_course_is_required(client):
    assert_rejected_on(client, REGISTER_URL, valid_payload(course_ids=[]), "course_ids")


def test_unknown_course_is_rejected(client):
    payload = valid_payload()
    payload["course_ids"].append(999999)

    assert_rejected_on(client, REGISTER_URL, payload, "course_ids")


def test_every_course_must_belong_to_the_chosen_institute(client):
    other = Course.objects.create(
        institute=Institute.objects.create(name="Outro instituto"), name="Pedagogia"
    )
    payload = valid_payload()
    payload["course_ids"].append(other.id)

    assert_rejected_on(client, REGISTER_URL, payload, "course_ids")


def test_at_least_one_area_of_interest_is_required(client):
    assert_rejected_on(client, REGISTER_URL, valid_payload(area_ids=[]), "area_ids")


def test_unknown_institute_is_rejected(client):
    assert_rejected_on(client, REGISTER_URL, valid_payload(institute_id=999999), "institute_id")


def test_registration_number_longer_than_the_database_allows_is_rejected(client):
    payload = valid_payload(registration_number="1" * 31)

    assert_rejected_on(client, REGISTER_URL, payload, "registration_number")


def test_all_errors_are_reported_together(client):
    payload = valid_payload(email="invalido", course_ids=[], area_ids=[])

    response = register(client, payload)

    assert set(errors_by_field(response)) == {"email", "course_ids", "area_ids"}
