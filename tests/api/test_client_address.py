import re
from pathlib import Path

import pytest
from django.test import RequestFactory, override_settings

from config.client_ip import client_ip
from tests.api.helpers import LOGIN_URL, post_json

BACKEND_ROOT = Path(__file__).resolve().parents[2]
RATE_LIMIT_VARIABLES = ["NUM_PROXIES", "CACHE_URL"] + [
    f"AUTH_{scope}_{suffix}"
    for scope in (
        "LOGIN_FAILURES",
        "CHANGE_PASSWORD_FAILURES",
        "LOGIN_IP",
        "REFRESH_IP",
        "LOGOUT_IP",
        "RESET_IP",
        "FORGOT_IP",
    )
    for suffix in ("LIMIT", "WINDOW_SECONDS")
]


def request_from(remote_address, forwarded_for=None):
    extra = {"REMOTE_ADDR": remote_address}
    if forwarded_for is not None:
        extra["HTTP_X_FORWARDED_FOR"] = forwarded_for
    return RequestFactory().get("/", **extra)


@override_settings(NUM_PROXIES=0)
def test_without_trusted_proxies_the_forwarded_header_is_ignored():
    request = request_from("10.0.0.5", "203.0.113.7")

    assert client_ip(request) == "10.0.0.5"


@override_settings(NUM_PROXIES=1)
def test_with_one_proxy_the_address_it_appended_is_the_client():
    request = request_from("172.18.0.3", "203.0.113.7")

    assert client_ip(request) == "203.0.113.7"


@override_settings(NUM_PROXIES=1)
def test_with_one_proxy_addresses_a_client_put_in_the_header_are_not_trusted():
    request = request_from("172.18.0.3", "1.2.3.4, 203.0.113.7")

    assert client_ip(request) == "203.0.113.7"


@override_settings(NUM_PROXIES=2)
def test_with_two_proxies_the_second_from_the_end_is_the_client():
    request = request_from("172.18.0.3", "1.2.3.4, 203.0.113.7, 10.1.1.1")

    assert client_ip(request) == "203.0.113.7"


@override_settings(NUM_PROXIES=3)
def test_with_fewer_addresses_than_proxies_the_first_one_is_used():
    request = request_from("172.18.0.3", "203.0.113.7")

    assert client_ip(request) == "203.0.113.7"


@override_settings(NUM_PROXIES=1)
def test_without_the_forwarded_header_the_connection_address_is_used():
    assert client_ip(request_from("172.18.0.3")) == "172.18.0.3"


@pytest.mark.django_db
@override_settings(NUM_PROXIES=0)
def test_changing_the_forwarded_header_does_not_dodge_the_limit_without_trusted_proxies(browser):
    for n in range(5):
        post_json(
            browser,
            LOGIN_URL,
            {"email": "ana@example.com", "password": "errada"},
            HTTP_X_FORWARDED_FOR=f"203.0.113.{n}",
        )

    response = post_json(
        browser,
        LOGIN_URL,
        {"email": "ana@example.com", "password": "errada"},
        HTTP_X_FORWARDED_FOR="198.51.100.99",
    )

    assert response.status_code == 429


def test_the_compose_setup_trusts_exactly_the_vite_proxy_hop():
    compose = (BACKEND_ROOT / "compose.yaml").read_text()

    assert re.search(r"^\s+NUM_PROXIES: \$\{NUM_PROXIES:-1\}\s*$", compose, re.MULTILINE)


def test_the_compose_setup_passes_every_rate_limit_variable_to_the_backend():
    compose = (BACKEND_ROOT / "compose.yaml").read_text()

    missing = [name for name in RATE_LIMIT_VARIABLES if f"      {name}: ${{{name}:-" not in compose]
    assert missing == []


def test_the_environment_example_documents_every_rate_limit_variable():
    example = (BACKEND_ROOT / ".env.example").read_text()

    assert [name for name in RATE_LIMIT_VARIABLES if f"{name}=" not in example] == []


def test_the_environment_example_does_not_enable_the_proxy_trust_by_default():
    lines = (BACKEND_ROOT / ".env.example").read_text().splitlines()
    active = [line for line in lines if not line.startswith("#")]

    assert not any(line.startswith("NUM_PROXIES") for line in active)


@override_settings(NUM_PROXIES=1)
@pytest.mark.parametrize("forged", ["203.0.113.7:51234", "not-an-address", "", "999.1.1.1"])
def test_a_malformed_forwarded_address_falls_back_to_the_connection_address(forged):
    request = request_from("172.18.0.3", forged)

    assert client_ip(request) == "172.18.0.3"


@override_settings(NUM_PROXIES=1)
def test_an_ipv6_client_address_is_accepted():
    request = request_from("172.18.0.3", "2001:db8::1")

    assert client_ip(request) == "2001:db8::1"


def test_a_missing_connection_address_gets_a_single_placeholder():
    request = RequestFactory().get("/")
    request.META.pop("REMOTE_ADDR", None)

    assert client_ip(request) == "unknown"
