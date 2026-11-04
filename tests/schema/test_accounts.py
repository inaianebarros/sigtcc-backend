import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

pytestmark = pytest.mark.django_db

User = get_user_model()


def create_user(email, **extra):
    return User.objects.create_user(
        email=email, password="senha-longa-123", full_name="Ana Souza", type="student", **extra
    )


def test_email_must_be_unique_ignoring_case():
    create_user("Ana.Souza@Example.com")

    with pytest.raises(IntegrityError), transaction.atomic():
        create_user("ana.souza@example.com")


def test_password_is_stored_hashed_never_in_plain_text():
    user = create_user("ana@example.com")
    user.refresh_from_db()

    assert user.password != "senha-longa-123"
    assert user.check_password("senha-longa-123")
