from django.db import transaction
from django.http import HttpResponse

from config.errors import FieldValidationError

from .models import User
from .passwords import validate_new_password
from .sessions import revoke_user_sessions, start_session

WRONG_CURRENT_PASSWORD = "A senha atual está incorreta."
SAME_AS_CURRENT_PASSWORD = "A nova senha deve ser diferente da atual."


def _validation_errors(
    user: User, current_password: str, password: str, confirmation: str
) -> dict[str, str]:
    errors = validate_new_password(password, confirmation, user)
    if not user.check_password(current_password):
        errors["current_password"] = WRONG_CURRENT_PASSWORD
    elif user.check_password(password):
        errors["password"] = SAME_AS_CURRENT_PASSWORD
    return errors


@transaction.atomic
def change_password(
    user: User, current_password: str, password: str, confirmation: str, response: HttpResponse
) -> str:
    locked_user = User.objects.select_for_update().get(pk=user.pk)
    errors = _validation_errors(locked_user, current_password, password, confirmation)
    if errors:
        raise FieldValidationError(errors)
    locked_user.set_password(password)
    locked_user.save(update_fields=["password"])
    revoke_user_sessions(locked_user.id)
    return start_session(locked_user, response)
