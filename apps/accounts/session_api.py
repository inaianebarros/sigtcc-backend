from django.conf import settings
from django.http import HttpRequest, HttpResponse
from ninja.responses import Status
from ninja_extra import api_controller, http_get, http_post
from ninja_extra.exceptions import AuthenticationFailed
from ninja_jwt.authentication import JWTAuth

from config.rate_limit import enforce_ip_limit
from config.schemas import ErrorOut

from .login_attempts import clear_failures, ensure_not_blocked, record_failure
from .models import User
from .schemas import AccessTokenOut, LoginIn, LoginOut, SessionUserOut
from .sessions import (
    authenticate_credentials,
    end_session,
    ensure_trusted_origin,
    rotate_session,
    start_session,
)


def _user_out(user: User) -> SessionUserOut:
    return SessionUserOut(id=user.id, full_name=user.full_name, email=user.email, type=user.type)


@api_controller("/auth", tags=["Autenticação"])
class SessionController:
    @http_post(
        "/login",
        response={200: LoginOut, 429: ErrorOut},
        summary="Entra com e-mail e senha",
    )
    def login(self, request: HttpRequest, payload: LoginIn, response: HttpResponse):
        enforce_ip_limit("login_ip", request)
        ensure_trusted_origin(request)
        ensure_not_blocked(payload.email, request)
        try:
            user = authenticate_credentials(payload.email, payload.password)
        except AuthenticationFailed:
            record_failure(payload.email, request)
            raise
        clear_failures(payload.email, request)
        access_token = start_session(user, response)
        return Status(200, LoginOut(access_token=access_token, user=_user_out(user)))

    @http_get("/me", response=SessionUserOut, auth=JWTAuth(), summary="Usuário da sessão")
    def me(self, request: HttpRequest):
        return _user_out(request.user)

    @http_post(
        "/refresh",
        response={200: AccessTokenOut, 429: ErrorOut},
        summary="Renova a sessão pelo cookie de refresh",
    )
    def refresh(self, request: HttpRequest, response: HttpResponse):
        enforce_ip_limit("refresh_ip", request)
        ensure_trusted_origin(request)
        refresh_token = request.COOKIES.get(settings.JWT_REFRESH_COOKIE_NAME)
        return Status(200, AccessTokenOut(access_token=rotate_session(refresh_token, response)))

    @http_post("/logout", response={204: None, 429: ErrorOut}, summary="Encerra a sessão")
    def logout(self, request: HttpRequest, response: HttpResponse):
        enforce_ip_limit("logout_ip", request)
        ensure_trusted_origin(request)
        end_session(request.COOKIES.get(settings.JWT_REFRESH_COOKIE_NAME), response)
        return Status(204, None)
