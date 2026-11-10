import logging
from smtplib import SMTPException

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.db import transaction
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from ninja_extra.exceptions import APIException

from config.errors import FieldValidationError

from .models import User
from .passwords import validate_new_password
from .sessions import revoke_user_sessions

logger = logging.getLogger(__name__)

EMAIL_SUBJECT = "Redefinição de senha - SIGTCC"
EMAIL_BODY = """Olá, {name}.

Recebemos um pedido para redefinir a senha da sua conta no SIGTCC. Para criar uma nova senha, acesse o link abaixo (válido por 1 hora e utilizável uma única vez):

{link}

Se você não fez esse pedido, ignore este e-mail: a sua senha continua a mesma.
"""


class InvalidResetLink(APIException):
    status_code = 400
    default_detail = "Link de redefinição inválido ou expirado."


def send_reset_link(email: str) -> None:
    user = User.objects.filter(email__iexact=email, is_active=True).first()
    if user is None:
        return
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    link = f"{settings.FRONTEND_URL}/redefinir-senha?uid={uid}&token={token}"
    body = EMAIL_BODY.format(name=user.full_name, link=link)
    try:
        send_mail(EMAIL_SUBJECT, body, None, [user.email])
    except (OSError, SMTPException):
        logger.exception("failed to send the password reset e-mail to user %s", user.pk)


def _user_for_link(uid: str, token: str) -> User:
    # Locks the user: used inside `reset_password`'s transaction, it makes a link single-use even
    # under concurrency and keeps any session from being created before the revocation.
    try:
        user = User.objects.select_for_update().get(
            pk=urlsafe_base64_decode(uid).decode(), is_active=True
        )
    except (User.DoesNotExist, ValueError, TypeError, OverflowError) as error:
        raise InvalidResetLink() from error
    if not default_token_generator.check_token(user, token):
        raise InvalidResetLink()
    return user


@transaction.atomic
def reset_password(uid: str, token: str, password: str, confirmation: str) -> None:
    user = _user_for_link(uid, token)
    errors = validate_new_password(password, confirmation, user)
    if errors:
        raise FieldValidationError(errors)
    user.set_password(password)
    user.save(update_fields=["password"])
    revoke_user_sessions(user.id)
