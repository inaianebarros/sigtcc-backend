from django.conf import settings
from django.http import HttpRequest, HttpResponse
from ninja.responses import Status
from ninja_extra import api_controller, http_get, http_post
from ninja_jwt.authentication import JWTAuth

from .schemas import AccessTokenOut, LoginIn, LoginOut, SessionUserOut
from .sessions import (
    authenticate_credentials,
    end_session,
    ensure_trusted_origin,
    rotate_session,
    start_session,
)


def _user_out(user) -> SessionUserOut:
    return SessionUserOut(id=user.id, full_name=user.full_name, email=user.email, type=user.type)


@api_controller("/auth", tags=["Autenticação"])
class SessionController:
    @http_post("/login", response=LoginOut, summary="Entra com e-mail e senha")
    def login(self, request: HttpRequest, payload: LoginIn, response: HttpResponse):
        ensure_trusted_origin(request)
        user = authenticate_credentials(payload.email, payload.password)
        access_token = start_session(user, response)
        return LoginOut(access_token=access_token, user=_user_out(user))

    @http_get("/me", response=SessionUserOut, auth=JWTAuth(), summary="Usuário da sessão")
    def me(self, request: HttpRequest):
        return _user_out(request.user)

    @http_post(
        "/refresh", response=AccessTokenOut, summary="Renova a sessão pelo cookie de refresh"
    )
    def refresh(self, request: HttpRequest, response: HttpResponse):
        ensure_trusted_origin(request)
        refresh_token = request.COOKIES.get(settings.JWT_REFRESH_COOKIE_NAME)
        return AccessTokenOut(access_token=rotate_session(refresh_token, response))

    @http_post("/logout", response={204: None}, summary="Encerra a sessão")
    def logout(self, request: HttpRequest, response: HttpResponse):
        ensure_trusted_origin(request)
        end_session(request.COOKIES.get(settings.JWT_REFRESH_COOKIE_NAME), response)
        return Status(204, None)
