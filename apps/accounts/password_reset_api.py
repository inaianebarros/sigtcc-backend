from django.http import HttpRequest
from ninja.responses import Status
from ninja_extra import api_controller, http_post

from config.rate_limit import enforce_ip_limit
from config.schemas import ErrorOut

from .password_reset import reset_password, send_reset_link
from .schemas import ForgotPasswordIn, MessageOut, ResetPasswordIn

FORGOT_PASSWORD_RESPONSE = (
    "Se o e-mail estiver cadastrado, enviaremos um link para redefinir a senha."
)
PASSWORD_CHANGED_RESPONSE = "Senha redefinida com sucesso."


@api_controller("/auth", tags=["Autenticação"])
class PasswordResetController:
    @http_post(
        "/forgot-password",
        response={200: MessageOut, 429: ErrorOut},
        summary="Envia o link de redefinição de senha",
        description="A resposta é sempre a mesma, exista ou não uma conta ativa com o e-mail.",
    )
    def request_reset_link(self, request: HttpRequest, payload: ForgotPasswordIn):
        enforce_ip_limit("forgot_ip", request)
        send_reset_link(payload.email)
        return Status(200, MessageOut(detail=FORGOT_PASSWORD_RESPONSE))

    @http_post(
        "/reset-password",
        response={200: MessageOut, 429: ErrorOut},
        summary="Define uma nova senha com o link recebido por e-mail",
    )
    def confirm_password_reset(self, request: HttpRequest, payload: ResetPasswordIn):
        enforce_ip_limit("reset_ip", request)
        reset_password(payload.uid, payload.token, payload.password, payload.password_confirmation)
        return Status(200, MessageOut(detail=PASSWORD_CHANGED_RESPONSE))
