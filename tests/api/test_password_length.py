"""A password longer than 128 characters is refused before it is hashed (#88, RNF05)."""

from unittest.mock import patch

import pytest

from apps.accounts.models import User
from tests.api.helpers import LOGIN_URL, bearer, errors_by_field, post_json
from tests.factories import make_user

pytestmark = pytest.mark.django_db

MAX_LENGTH = 128
TOO_LONG = "A" + "a" * MAX_LENGTH
JUST_RIGHT = "A" + "a" * (MAX_LENGTH - 1)
MESSAGE = "A senha deve ter no máximo 128 caracteres."
STUDENT_URL = "/api/auth/register/aluno"
TEACHER_URL = "/api/auth/register/professor"
RESET_URL = "/api/auth/reset-password"
CHANGE_URL = "/api/auth/change-password"


def registration(password):
    return {
        "registration_number": "202411001",
        "full_name": "Ana Souza",
        "email": "ana.souza@example.com",
        "password": password,
        "password_confirmation": password,
        "institute_id": 1,
        "area_ids": [1],
    }


def request_with(url, body, headers=None):
    return url, body, headers or {}


def login_case():
    return request_with(LOGIN_URL, {"email": "ana@example.com", "password": TOO_LONG}), ["password"]


def student_case():
    body = {**registration(TOO_LONG), "course_id": 1}
    return request_with(STUDENT_URL, body), ["password", "password_confirmation"]


def teacher_case():
    body = {**registration(TOO_LONG), "course_ids": [1]}
    return request_with(TEACHER_URL, body), ["password", "password_confirmation"]


def reset_case():
    body = {
        "uid": "x",
        "token": "y",
        "password": TOO_LONG,
        "password_confirmation": TOO_LONG,
    }
    return request_with(RESET_URL, body), ["password", "password_confirmation"]


def change_case():
    user = make_user()
    body = {
        "current_password": TOO_LONG,
        "password": TOO_LONG,
        "password_confirmation": TOO_LONG,
    }
    return request_with(CHANGE_URL, body, bearer(user)), [
        "current_password",
        "password",
        "password_confirmation",
    ]


CASES = [login_case, student_case, teacher_case, reset_case, change_case]
IDS = ["login", "student", "teacher", "reset", "change"]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_a_password_over_the_limit_is_rejected_with_422_on_each_password_field(browser, case):
    (url, body, headers), fields = case()

    response = post_json(browser, url, body, headers=headers)

    assert response.status_code == 422
    errors = errors_by_field(response)
    for field in fields:
        assert errors[field] == MESSAGE


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_a_password_over_the_limit_is_never_hashed(browser, case):
    (url, body, headers), _ = case()

    with (
        patch.object(User, "set_password") as set_password,
        patch.object(User, "check_password") as check_password,
        patch("apps.accounts.sessions.User.set_password") as unknown_account_hash,
    ):
        response = post_json(browser, url, body, headers=headers)

    assert response.status_code == 422
    set_password.assert_not_called()
    check_password.assert_not_called()
    unknown_account_hash.assert_not_called()


def test_a_password_of_exactly_the_limit_goes_on_to_the_normal_flow(browser):
    make_user(email="ana@example.com")

    response = post_json(browser, LOGIN_URL, {"email": "ana@example.com", "password": JUST_RIGHT})

    assert response.status_code == 401


def test_the_limit_lives_in_one_shared_type_and_the_openapi_documents_it(client):
    schemas = client.get("/api/openapi.json").json()["components"]["schemas"]

    password_fields = [
        ("LoginIn", "password"),
        ("StudentRegistrationIn", "password"),
        ("StudentRegistrationIn", "password_confirmation"),
        ("TeacherRegistrationIn", "password"),
        ("TeacherRegistrationIn", "password_confirmation"),
        ("ResetPasswordIn", "password"),
        ("ResetPasswordIn", "password_confirmation"),
        ("ChangePasswordIn", "current_password"),
        ("ChangePasswordIn", "password"),
        ("ChangePasswordIn", "password_confirmation"),
    ]
    for schema, field in password_fields:
        assert schemas[schema]["properties"][field]["maxLength"] == MAX_LENGTH, (schema, field)
