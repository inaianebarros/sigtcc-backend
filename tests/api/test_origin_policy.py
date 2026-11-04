import pytest

from tests.api.helpers import LOGIN_URL, LOGOUT_URL, REFRESH_URL, post_json
from tests.factories import DEFAULT_PASSWORD, make_user

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("url", [LOGIN_URL, REFRESH_URL, LOGOUT_URL])
def test_session_endpoints_reject_requests_without_an_origin(client, url):
    user = make_user(email="ana@example.com")
    payload = {"email": user.email, "password": DEFAULT_PASSWORD}

    response = post_json(client, url, payload)

    assert response.status_code == 403


@pytest.mark.parametrize("url", [LOGIN_URL, REFRESH_URL, LOGOUT_URL])
def test_session_endpoints_reject_untrusted_origins(client, url):
    user = make_user(email="ana@example.com")
    payload = {"email": user.email, "password": DEFAULT_PASSWORD}

    response = post_json(client, url, payload, HTTP_ORIGIN="https://evil.example")

    assert response.status_code == 403
