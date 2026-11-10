import pytest
from django.core.cache import cache
from django.test import Client

from tests.factories import make_admin

TRUSTED_ORIGIN = "http://localhost:3000"


@pytest.fixture
def browser():
    return Client(HTTP_ORIGIN=TRUSTED_ORIGIN)


@pytest.fixture(autouse=True)
def clean_rate_limit_counters():
    cache.clear()


@pytest.fixture(autouse=True)
def fast_password_hasher(settings):
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@pytest.fixture(autouse=True)
def in_memory_submission_storage(settings):
    """The API tests never reach the real bucket; only the integration test does (ADR 0002)."""
    settings.STORAGES = {
        **settings.STORAGES,
        "submissions": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    }


@pytest.fixture
def admin_client(client, db):
    """A client logged in to the Django Admin as the administrator (session, not JWT)."""
    client.force_login(make_admin())
    return client
