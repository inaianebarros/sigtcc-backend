import pytest
from django.core.cache import cache
from django.test import Client

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
