import pytest
from django.test import Client

TRUSTED_ORIGIN = "http://localhost:3000"


@pytest.fixture
def browser():
    return Client(HTTP_ORIGIN=TRUSTED_ORIGIN)
