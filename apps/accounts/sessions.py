from django.conf import settings
from django.http import HttpRequest, HttpResponse
from ninja_extra.exceptions import AuthenticationFailed, PermissionDenied
from ninja_jwt.exceptions import TokenError
from ninja_jwt.tokens import RefreshToken

from .models import User

INVALID_CREDENTIALS = "E-mail ou senha inválidos."
INVALID_SESSION = "Sessão inválida ou expirada."


def authenticate_credentials(email: str, password: str) -> User:
    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        User().set_password(password)
        raise AuthenticationFailed(INVALID_CREDENTIALS)
    if not user.check_password(password) or not user.is_active:
        raise AuthenticationFailed(INVALID_CREDENTIALS)
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
    # Browsers always send Origin on cross-site POSTs; clients without it are not CSRF targets.
    origin = request.headers.get("Origin")
    if origin is not None and origin not in settings.JWT_TRUSTED_ORIGINS:
        raise PermissionDenied("Origem não autorizada.")


def rotate_session(refresh_token: str | None, response: HttpResponse) -> str:
    if not refresh_token:
        raise AuthenticationFailed(INVALID_SESSION)
    try:
        current = RefreshToken(refresh_token)
        user = User.objects.get(id=current["user_id"])
    except (TokenError, KeyError, User.DoesNotExist) as error:
        raise AuthenticationFailed(INVALID_SESSION) from error
    if not user.is_active:
        raise AuthenticationFailed(INVALID_SESSION)

    # blacklist() returns (record, created): "created" is false when another request already used it
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
