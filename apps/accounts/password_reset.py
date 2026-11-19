import hashlib
import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from ninja_extra.exceptions import APIException

from config import rate_limit
from config.errors import FieldValidationError

from .demo_seed import is_demo_account
from .failure_limit import normalize_email
from .models import PasswordResetRequest, User
from .passwords import validate_new_password
from .sessions import revoke_user_sessions

logger = logging.getLogger("sigtcc.auth")

#: After this many failed attempts the request is given up (ADR 0020, as ADR 0011 does).
MAX_SEND_ATTEMPTS = 5

#: A request not sent in this time is discarded: the person has asked again or given up by then.
REQUEST_VALID_FOR = timedelta(minutes=15)
#: A settled request is kept this long, only so the queue does not grow without limit.
KEEP_SETTLED_FOR = timedelta(days=1)

EMAIL_SUBJECT = "Redefinição de senha - SIGTCC"
EMAIL_BODY = """Olá, {name}.

Recebemos um pedido para redefinir a senha da sua conta no SIGTCC. Para criar uma nova senha, acesse o link abaixo (válido por 1 hora e utilizável uma única vez):

{link}

Se você não fez esse pedido, ignore este e-mail: a sua senha continua a mesma.
"""


class InvalidResetLink(APIException):
    status_code = 400
    default_detail = "Link de redefinição inválido ou expirado."


def within_account_limit(email: str) -> bool:
    """Counts a request against the e-mail, whatever the address and whether or not it has an
    account, and says whether it is still within the limit (RNF25, ADR 0020).

    The key has no client address on purpose: the limit protects the owner of the e-mail from
    requests made from many addresses. A broken cache lets the request through."""
    limit, window = settings.AUTH_RATE_LIMITS["forgot_account"]
    digest = hashlib.sha256(normalize_email(email).encode()).hexdigest()
    try:
        count, retry_after = rate_limit.hit(f"rl:forgot_account:{digest}", window)
    except Exception:
        rate_limit.fail_open("forgot_account")
        return True
    if count > limit:
        logger.warning(
            "reset request limited",
            extra={"event": "rate_limited", "scope": "forgot_account", "retry_after": retry_after},
        )
        return False
    return True


def request_reset_link(email: str) -> None:
    """Queues a request for the e-mail, the same work whether or not it has an active account.

    The link is not made nor sent here: the `send_password_reset_emails` command does it, so the
    time of the answer says nothing about which e-mails exist (ADR 0020)."""
    user = User.objects.filter(email__iexact=email, is_active=True).first()
    PasswordResetRequest.objects.create(user=user)


def discard_stale_requests() -> int:
    """Also settles the requests given up after too many failed attempts, once they get this old."""
    return PasswordResetRequest.objects.filter(
        processed_at__isnull=True, created_at__lt=timezone.now() - REQUEST_VALID_FOR
    ).update(processed_at=timezone.now())


def delete_old_settled_requests() -> int:
    """Keeps the queue from growing: see `KEEP_SETTLED_FOR`."""
    deleted, _ = PasswordResetRequest.objects.filter(
        processed_at__lt=timezone.now() - KEEP_SETTLED_FOR
    ).delete()
    return deleted


def pending_request_ids() -> list[int]:
    """The ids of the requests still to be settled, oldest first."""
    return list(
        PasswordResetRequest.objects.filter(
            processed_at__isnull=True, attempts__lt=MAX_SEND_ATTEMPTS
        )
        .order_by("id")
        .values_list("id", flat=True)
    )


def lock_pending_request(request_id: int) -> PasswordResetRequest | None:
    """Locks the request for the time of its settling, or returns None when another run holds it or
    it was settled meanwhile. Call it inside a transaction."""
    return (
        PasswordResetRequest.objects.select_for_update(skip_locked=True, of=("self",))
        .select_related("user")
        .filter(pk=request_id, processed_at__isnull=True, attempts__lt=MAX_SEND_ATTEMPTS)
        .first()
    )


def record_failed_attempt(request_id: int) -> None:
    """Counts a failed attempt; at the last one the request is given up and it is logged."""
    # Only a request still pending counts: another run may have settled it after this one failed.
    counted = PasswordResetRequest.objects.filter(pk=request_id, processed_at__isnull=True).update(
        attempts=F("attempts") + 1
    )
    if not counted:
        return
    attempts = PasswordResetRequest.objects.values_list("attempts", flat=True).get(pk=request_id)
    if attempts >= MAX_SEND_ATTEMPTS:
        logger.error(
            "gave up e-mailing password reset request %s after %s attempts", request_id, attempts
        )


def send_reset_link(user: User) -> None:
    """E-mails a link made now, so its hour counts from the e-mail (RNF08)."""
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    link = f"{settings.FRONTEND_URL}/redefinir-senha?uid={uid}&token={token}"
    body = EMAIL_BODY.format(name=user.full_name, link=link)
    send_mail(EMAIL_SUBJECT, body, None, [user.email])


def settle_request(request: PasswordResetRequest) -> bool:
    """Sends the link of a request, or just settles it when there is no active, non-simulated
    account to send to (ADR 0022). Returns whether an e-mail went out."""
    user = request.user
    mailed = False
    if user is not None and user.is_active and not is_demo_account(user.email):
        send_reset_link(user)
        mailed = True
    request.processed_at = timezone.now()
    request.save(update_fields=["processed_at"])
    return mailed


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
