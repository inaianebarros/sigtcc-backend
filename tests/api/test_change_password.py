import pytest
from django.http import HttpResponse
from django.test import Client

from apps.accounts.models import UserType
from apps.accounts.password_change import change_password as change_password_of
from config.errors import FieldValidationError
from tests.api.helpers import (
    LOGIN_URL,
    REFRESH_COOKIE,
    REFRESH_URL,
    TOO_MANY_REQUESTS,
    errors_by_field,
    post_json,
    travel,
)
from tests.factories import DEFAULT_PASSWORD, make_user

pytestmark = pytest.mark.django_db

CHANGE_PASSWORD_URL = "/api/auth/change-password"
NEW_PASSWORD = "Nova-Senha-Segura-2026"


def sign_in(browser, user):
    response = post_json(browser, LOGIN_URL, {"email": user.email, "password": DEFAULT_PASSWORD})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def change_password(browser, headers, **overrides):
    payload = {
        "current_password": DEFAULT_PASSWORD,
        "password": NEW_PASSWORD,
        "password_confirmation": NEW_PASSWORD,
        **overrides,
    }
    return post_json(browser, CHANGE_PASSWORD_URL, payload, headers=headers)


def can_log_in(client, user, password):
    response = post_json(client, LOGIN_URL, {"email": user.email, "password": password})
    return response.status_code == 200


def test_a_valid_change_makes_the_new_password_work_and_the_old_one_stop(browser):
    user = make_user()
    headers = sign_in(browser, user)

    response = change_password(browser, headers)

    assert response.status_code == 200
    assert response.json()["access_token"]
    assert can_log_in(browser, user, NEW_PASSWORD)
    assert not can_log_in(browser, user, DEFAULT_PASSWORD)


def test_a_wrong_current_password_is_rejected_and_changes_nothing(browser):
    user = make_user()
    headers = sign_in(browser, user)

    response = change_password(browser, headers, current_password="senha-errada-123")

    assert response.status_code == 422
    assert errors_by_field(response) == {"current_password": "A senha atual está incorreta."}
    assert can_log_in(browser, user, DEFAULT_PASSWORD)
    assert not can_log_in(browser, user, NEW_PASSWORD)


def test_a_new_password_equal_to_the_current_one_is_rejected(browser):
    user = make_user()
    headers = sign_in(browser, user)

    response = change_password(
        browser, headers, password=DEFAULT_PASSWORD, password_confirmation=DEFAULT_PASSWORD
    )

    assert response.status_code == 422
    assert errors_by_field(response) == {"password": "A nova senha deve ser diferente da atual."}


def test_every_validation_error_comes_at_once_on_its_own_field(browser):
    user = make_user()
    headers = sign_in(browser, user)

    response = change_password(
        browser,
        headers,
        current_password="senha-errada-123",
        password="123",
        password_confirmation="456",
    )

    assert response.status_code == 422
    errors = errors_by_field(response)
    assert set(errors) == {"current_password", "password", "password_confirmation"}
    assert errors["password_confirmation"] == "As senhas não coincidem."
    assert can_log_in(browser, user, DEFAULT_PASSWORD)


def test_a_weak_new_password_is_rejected_and_nothing_changes(browser):
    user = make_user()
    headers = sign_in(browser, user)

    response = change_password(
        browser, headers, password="password", password_confirmation="password"
    )

    assert response.status_code == 422
    assert set(errors_by_field(response)) == {"password"}
    assert can_log_in(browser, user, DEFAULT_PASSWORD)


def test_a_change_ends_the_other_sessions_and_keeps_the_current_device_signed_in(browser):
    user = make_user()
    other_device = Client(HTTP_ORIGIN=browser.defaults["HTTP_ORIGIN"])
    sign_in(other_device, user)
    headers = sign_in(browser, user)

    response = change_password(browser, headers)

    assert response.status_code == 200
    assert browser.post(REFRESH_URL).status_code == 200
    assert other_device.post(REFRESH_URL).status_code == 401


def test_the_refresh_token_issued_before_the_change_is_rejected(browser):
    user = make_user()
    headers = sign_in(browser, user)
    old_refresh_token = browser.cookies[REFRESH_COOKIE].value

    change_password(browser, headers)
    browser.cookies[REFRESH_COOKIE] = old_refresh_token

    assert browser.post(REFRESH_URL).status_code == 401


@pytest.mark.parametrize("origin", [None, "https://evil.example"])
def test_a_missing_or_untrusted_origin_is_forbidden_and_changes_nothing(browser, client, origin):
    user = make_user()
    headers = sign_in(browser, user)
    extra = {} if origin is None else {"HTTP_ORIGIN": origin}

    response = post_json(
        client,
        CHANGE_PASSWORD_URL,
        {
            "current_password": DEFAULT_PASSWORD,
            "password": NEW_PASSWORD,
            "password_confirmation": NEW_PASSWORD,
        },
        headers=headers,
        **extra,
    )

    assert response.status_code == 403
    assert can_log_in(browser, user, DEFAULT_PASSWORD)


def test_an_anonymous_request_is_not_authenticated(browser):
    response = change_password(browser, headers={})

    assert response.status_code == 401


@pytest.mark.parametrize("user_type", [UserType.STUDENT, UserType.TEACHER, UserType.ADMIN])
def test_every_kind_of_user_can_change_their_own_password(browser, user_type):
    user = make_user(user_type)
    headers = sign_in(browser, user)

    response = change_password(browser, headers)

    assert response.status_code == 200
    assert can_log_in(browser, user, NEW_PASSWORD)


def test_the_new_password_is_stored_as_a_hash_and_never_echoed_or_logged(browser, caplog):
    user = make_user()
    headers = sign_in(browser, user)

    with caplog.at_level("DEBUG"):
        response = change_password(browser, headers)

    user.refresh_from_db()
    assert user.password != NEW_PASSWORD
    assert "$" in user.password
    assert NEW_PASSWORD not in response.content.decode()
    assert NEW_PASSWORD not in caplog.text
    assert DEFAULT_PASSWORD not in caplog.text


FAILURE_LIMIT = 5


def fail_current_password(browser, headers, times):
    return [
        change_password(browser, headers, current_password="senha-errada-123").status_code
        for _ in range(times)
    ]


def test_after_five_wrong_current_passwords_the_change_is_blocked_even_with_the_right_one(
    browser,
):
    user = make_user()
    headers = sign_in(browser, user)

    failures = fail_current_password(browser, headers, FAILURE_LIMIT)
    blocked = change_password(browser, headers)

    assert failures == [422] * FAILURE_LIMIT
    assert blocked.status_code == 429
    assert blocked.json() == TOO_MANY_REQUESTS
    assert 0 < int(blocked["Retry-After"]) <= 15 * 60
    assert can_log_in(browser, user, DEFAULT_PASSWORD)


def test_the_change_works_again_after_the_window_ends(browser):
    user = make_user()
    headers = sign_in(browser, user)
    fail_current_password(browser, headers, FAILURE_LIMIT)
    assert change_password(browser, headers).status_code == 429

    with travel(seconds=15 * 60 + 1):
        assert change_password(browser, headers).status_code == 200


def test_a_successful_change_resets_the_failure_counter(browser):
    user = make_user()
    headers = sign_in(browser, user)
    fail_current_password(browser, headers, FAILURE_LIMIT - 1)
    assert change_password(browser, headers).status_code == 200

    after_reset = fail_current_password(browser, headers, FAILURE_LIMIT - 1)

    assert after_reset == [422] * (FAILURE_LIMIT - 1)
    back = change_password(
        browser,
        headers,
        current_password=NEW_PASSWORD,
        password=DEFAULT_PASSWORD,
        password_confirmation=DEFAULT_PASSWORD,
    )
    assert back.status_code == 200


def test_errors_on_the_new_password_do_not_count_as_failures(browser):
    user = make_user()
    headers = sign_in(browser, user)

    rejected = [
        change_password(browser, headers, password="123", password_confirmation="456").status_code
        for _ in range(FAILURE_LIMIT + 1)
    ]

    assert rejected == [422] * (FAILURE_LIMIT + 1)
    assert change_password(browser, headers).status_code == 200


def test_the_failures_of_one_account_do_not_block_another(browser):
    attacked = make_user()
    other = make_user()
    attacked_headers = sign_in(browser, attacked)
    fail_current_password(browser, attacked_headers, FAILURE_LIMIT)

    other_headers = sign_in(Client(HTTP_ORIGIN=browser.defaults["HTTP_ORIGIN"]), other)

    assert change_password(browser, other_headers).status_code == 200


def test_the_openapi_documents_the_endpoint_its_body_and_its_errors(client):
    schema = client.get("/api/openapi.json").json()

    operation = schema["paths"]["/api/auth/change-password"]["post"]
    assert {"200", "401", "403", "422", "429"} <= set(operation["responses"])
    body = operation["requestBody"]["content"]["application/json"]["schema"]
    assert body["$ref"].endswith("/ChangePasswordIn")
    assert set(schema["components"]["schemas"]["ChangePasswordIn"]["required"]) == {
        "current_password",
        "password",
        "password_confirmation",
    }


def test_a_request_that_loaded_the_user_before_a_concurrent_change_checks_the_fresh_password():
    user = make_user()
    stale_user = type(user).objects.get(pk=user.pk)
    user.set_password("Outra-Senha-Segura-2026")
    user.save(update_fields=["password"])

    with pytest.raises(FieldValidationError) as rejected:
        change_password_of(stale_user, DEFAULT_PASSWORD, NEW_PASSWORD, NEW_PASSWORD, HttpResponse())

    assert set(rejected.value.errors) == {"current_password"}
    user.refresh_from_db()
    assert user.check_password("Outra-Senha-Segura-2026")
