import pytest
from django.contrib.auth import get_user_model

from apps.accounts.models import Institute, Student, UserType
from tests.api.helpers import PASSWORD, assert_rejected_on, errors_by_field, post_json
from tests.factories import make_area, make_course, make_student, make_teacher

pytestmark = pytest.mark.django_db

REGISTER_URL = "/api/auth/register/aluno"


def valid_payload(**overrides):
    course = make_course()
    payload = {
        "registration_number": "202411001",
        "full_name": "Ana Souza",
        "email": "ana.souza@example.com",
        "password": PASSWORD,
        "password_confirmation": PASSWORD,
        "institute_id": course.institute_id,
        "course_id": course.id,
        "area_ids": [make_area().id, make_area().id],
    }
    payload.update(overrides)
    return payload


def register(client, payload):
    return post_json(client, REGISTER_URL, payload)


def test_a_visitor_registers_as_a_student(client):
    payload = valid_payload()

    response = register(client, payload)

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "ana.souza@example.com"
    assert body["full_name"] == "Ana Souza"
    assert body["type"] == "student"
    assert body["registration_number"] == "202411001"
    assert sorted(body["area_ids"]) == sorted(payload["area_ids"])
    assert "password" not in body and "password_confirmation" not in body

    student = Student.objects.get(user__email="ana.souza@example.com")
    assert student.user.type == UserType.STUDENT
    assert student.course_id == payload["course_id"]
    assert student.user.check_password(PASSWORD)
    assert student.user.password != PASSWORD
    assert set(student.areas_of_interest.values_list("id", flat=True)) == set(payload["area_ids"])
    assert get_user_model().objects.count() == 1


def test_duplicate_email_is_rejected_ignoring_case(client):
    assert register(client, valid_payload()).status_code == 201
    second = valid_payload(email="ANA.SOUZA@example.com", registration_number="202411002")

    assert_rejected_on(client, REGISTER_URL, second, "email")


def test_invalid_email_is_rejected(client):
    assert_rejected_on(client, REGISTER_URL, valid_payload(email="isto-nao-e-um-email"), "email")


def test_password_confirmation_must_match(client):
    payload = valid_payload(password_confirmation="Outra-Senha-2026")

    assert_rejected_on(client, REGISTER_URL, payload, "password_confirmation")


@pytest.mark.parametrize("weak_password", ["curta1", "12345678", "password"])
def test_weak_password_is_rejected(client, weak_password):
    payload = valid_payload(password=weak_password, password_confirmation=weak_password)

    assert_rejected_on(client, REGISTER_URL, payload, "password")


def test_missing_required_field_is_reported_on_that_field(client):
    payload = valid_payload()
    del payload["full_name"]

    assert_rejected_on(client, REGISTER_URL, payload, "full_name")


@pytest.mark.parametrize("field", ["registration_number", "full_name"])
def test_blank_text_fields_are_rejected(client, field):
    assert_rejected_on(client, REGISTER_URL, valid_payload(**{field: "   "}), field)


def test_unknown_institute_is_rejected(client):
    assert_rejected_on(client, REGISTER_URL, valid_payload(institute_id=999999), "institute_id")


def test_unknown_course_is_rejected(client):
    assert_rejected_on(client, REGISTER_URL, valid_payload(course_id=999999), "course_id")


def test_course_must_belong_to_the_chosen_institute(client):
    other_institute = Institute.objects.create(name="Outro instituto")
    payload = valid_payload(institute_id=other_institute.id)

    assert_rejected_on(client, REGISTER_URL, payload, "course_id")


def test_at_least_one_area_of_interest_is_required(client):
    assert_rejected_on(client, REGISTER_URL, valid_payload(area_ids=[]), "area_ids")


def test_unknown_area_of_interest_is_rejected(client):
    assert_rejected_on(
        client, REGISTER_URL, valid_payload(area_ids=[make_area().id, 999999]), "area_ids"
    )


def test_registration_number_must_be_unique_among_students(client):
    make_student(registration_number="202411001")

    assert_rejected_on(client, REGISTER_URL, valid_payload(), "registration_number")


def test_registration_number_may_match_a_teachers(client):
    make_teacher(registration_number="202411001")

    response = register(client, valid_payload())

    assert response.status_code == 201


def test_all_errors_are_reported_together_so_the_form_can_show_them_inline(client):
    payload = valid_payload(email="invalido", password_confirmation="outra", area_ids=[])

    response = register(client, payload)

    assert set(errors_by_field(response)) == {"email", "password_confirmation", "area_ids"}


@pytest.mark.parametrize(
    ("field", "too_long"),
    [
        ("full_name", "A" * 151),
        ("registration_number", "1" * 31),
        ("email", "a" * 250 + "@example.com"),
    ],
)
def test_values_longer_than_the_database_allows_are_rejected_on_that_field(client, field, too_long):
    assert_rejected_on(client, REGISTER_URL, valid_payload(**{field: too_long}), field)


def test_surrounding_whitespace_is_trimmed_before_saving(client):
    payload = valid_payload(full_name="  Ana Souza  ", registration_number=" 202411001 ")

    body = register(client, payload).json()

    assert body["full_name"] == "Ana Souza"
    assert body["registration_number"] == "202411001"
