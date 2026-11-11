import pytest
from django.conf import settings as django_settings
from django.core.cache import cache
from django.core.management import call_command
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


@pytest.fixture
def catalog(db):
    """The real catalog of the project, as `loaddata` brings it to a new environment."""
    call_command("loaddata", str(django_settings.BASE_DIR / "fixtures" / "seed.json"), verbosity=0)


@pytest.fixture
def debug(settings):
    """Local-environment mode: the demonstration command only runs with DEBUG on."""
    settings.DEBUG = True
