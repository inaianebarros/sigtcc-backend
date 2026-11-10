from django.conf import settings
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from ninja_extra.exceptions import AuthenticationFailed, PermissionDenied
from ninja_jwt.exceptions import TokenBackendError, TokenError
from ninja_jwt.state import token_backend
from ninja_jwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from ninja_jwt.tokens import RefreshToken

from .models import User

INVALID_CREDENTIALS = "E-mail ou senha inválidos."
INVALID_SESSION = "Sessão inválida ou expirada."


class InvalidCredentialsError(AuthenticationFailed):
    def __init__(self, user_id: int | None = None):
        super().__init__(INVALID_CREDENTIALS)
        self.user_id = user_id


def authenticate_credentials(email: str, password: str) -> User:
    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        User().set_password(password)
        raise InvalidCredentialsError()
    if not user.check_password(password) or not user.is_active:
        raise InvalidCredentialsError(user.id)
    return user


def start_session(user: User, response: HttpResponse) -> str:
    refresh = RefreshToken.for_user(user)
    _set_refresh_cookie(response, str(refresh))
    return str(refresh.access_token)


def _set_refresh_cookie(response: HttpResponse, token: str) -> None:
    response.set_cookie(
        settings.JWT_REFRESH_COOKIE_NAME,
        token,
        max_age=int(settings.NINJA_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds()),
        path=settings.JWT_REFRESH_COOKIE_PATH,
        secure=settings.JWT_REFRESH_COOKIE_SECURE,
        httponly=True,
        samesite="Lax",
    )


def ensure_trusted_origin(request: HttpRequest) -> None:
    # Browsers send Origin on every POST, so the application's own requests always carry it.
    if request.headers.get("Origin") not in settings.JWT_TRUSTED_ORIGINS:
        raise PermissionDenied("Origem não autorizada.")


def rotate_session(refresh_token: str | None, response: HttpResponse) -> str:
    if not refresh_token:
        raise AuthenticationFailed(INVALID_SESSION)
    try:
        current = RefreshToken(refresh_token)
    except TokenError as error:
        _revoke_sessions_if_token_was_reused(refresh_token)
        raise AuthenticationFailed(INVALID_SESSION) from error
    # The user's row lock serializes this rotation with every revocation of the same user (a
    # password change or reset, a deactivation): whichever runs second sees what the first did, so
    # no refresh token is created outside a revocation's snapshot (ADR 0006, RN27).
    with transaction.atomic():
        try:
            user = User.objects.select_for_update().get(id=current["user_id"])
        except (KeyError, User.DoesNotExist) as error:
            raise AuthenticationFailed(INVALID_SESSION) from error
        if not user.is_active:
            raise AuthenticationFailed(INVALID_SESSION)

        # blacklist() returns (record, created): "created" is false when another request already
        # used it, or when a revocation that ran while this request waited for the lock did.
        _, first_use = current.blacklist()
        if not first_use:
            raise AuthenticationFailed(INVALID_SESSION)
        return start_session(user, response)


def end_session(refresh_token: str | None, response: HttpResponse) -> None:
    if refresh_token:
        try:
            RefreshToken(refresh_token).blacklist()
        except TokenError:
            pass  # logout is idempotent: an invalid or expired token needs no blocking
    response.delete_cookie(settings.JWT_REFRESH_COOKIE_NAME, path=settings.JWT_REFRESH_COOKIE_PATH)


@transaction.atomic
def revoke_user_sessions(user_id: int) -> None:
    """Blacklists every live refresh token of the user.

    Takes the user's row lock first (a no-op when the caller already holds it), so a rotation that
    is running finishes before the snapshot of the live tokens is taken.
    """
    User.objects.select_for_update().filter(pk=user_id).first()
    active_tokens = OutstandingToken.objects.filter(user_id=user_id, blacklistedtoken__isnull=True)
    BlacklistedToken.objects.bulk_create(
        [BlacklistedToken(token=token) for token in active_tokens], ignore_conflicts=True
    )


def _revoke_sessions_if_token_was_reused(refresh_token: str) -> None:
    try:
        payload = token_backend.decode(refresh_token, verify=True)
    except TokenBackendError:
        return
    user_id = payload.get("user_id")
    already_used = BlacklistedToken.objects.filter(token__jti=payload.get("jti")).exists()
    if user_id is not None and already_used:
        revoke_user_sessions(user_id)
