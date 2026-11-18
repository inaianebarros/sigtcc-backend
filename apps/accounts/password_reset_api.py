from django.http import HttpRequest
from ninja.responses import Status
from ninja_extra import api_controller, http_post

from config.rate_limit import enforce_ip_limit
from config.schemas import ErrorOut

from .password_reset import request_reset_link, reset_password, within_account_limit
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
        description=(
            "A resposta é sempre a mesma, exista ou não uma conta ativa com o e-mail, e o trabalho "
            "da requisição também: o pedido só entra numa fila, e o e-mail sai depois, por um "
            "serviço agendado (ADR 0020), em até cerca de 1 minuto. Cada e-mail aceita 3 pedidos por "
            "hora; acima disso a resposta continua a mesma, sem 429, e nenhum e-mail é enviado."
        ),
    )
    def request_reset_link(self, request: HttpRequest, payload: ForgotPasswordIn):
        enforce_ip_limit("forgot_ip", request)
        if within_account_limit(payload.email):
            request_reset_link(payload.email)
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
