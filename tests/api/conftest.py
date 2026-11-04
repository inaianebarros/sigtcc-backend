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
