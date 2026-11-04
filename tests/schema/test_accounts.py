import pytest
from django.db import IntegrityError, transaction

from tests.factories import make_user

pytestmark = pytest.mark.django_db


def test_email_must_be_unique_ignoring_case():
    make_user(email="Ana.Souza@Example.com")

    with pytest.raises(IntegrityError), transaction.atomic():
        make_user(email="ana.souza@example.com")


def test_password_is_stored_hashed_never_in_plain_text():
    user = make_user()
    user.refresh_from_db()

    assert user.password != "senha-longa-123"
    assert user.check_password("senha-longa-123")
