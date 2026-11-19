import pytest

from apps.accounts.demo_seed import INSTITUTE, OWN_COURSE
from apps.accounts.models import Course, Institute
from tests.factories import make_area


@pytest.fixture(autouse=True)
def personas_enabled(settings):
    settings.PERSONAS_ENABLED = True
    settings.PERSONAS_MAX_ACTIONS_PER_CYCLE = 20


@pytest.fixture(autouse=True)
def fast_password_hasher(settings):
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@pytest.fixture(autouse=True)
def in_memory_submission_storage(settings):
    settings.STORAGES = {
        **settings.STORAGES,
        "submissions": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    }


@pytest.fixture
def course():
    institute = Institute.objects.create(name=INSTITUTE)
    return Course.objects.create(institute=institute, name=OWN_COURSE)


@pytest.fixture
def areas(course):
    return [make_area(), make_area()]
