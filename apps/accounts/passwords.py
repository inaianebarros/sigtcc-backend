from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from .models import User


def validate_new_password(password: str, confirmation: str, user: User) -> dict[str, str]:
    errors: dict[str, str] = {}
    if password != confirmation:
        errors["password_confirmation"] = "As senhas não coincidem."
    try:
        validate_password(password, user)
    except ValidationError as error:
        errors["password"] = " ".join(error.messages)
    return errors
