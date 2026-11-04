from ninja_extra import api_controller, http_post
from ninja_extra.throttling import AnonRateThrottle

from .password_reset import reset_password, send_reset_link
from .schemas import ForgotPasswordIn, MessageOut, ResetPasswordIn

FORGOT_PASSWORD_RESPONSE = (
    "Se o e-mail estiver cadastrado, enviaremos um link para redefinir a senha."
)
PASSWORD_CHANGED_RESPONSE = "Senha redefinida com sucesso."


class ResetRequestThrottle(AnonRateThrottle):
    scope = "password_reset_request"
    rate = "5/hour"


@api_controller("/auth", tags=["Autenticação"])
class PasswordResetController:
    @http_post(
        "/forgot-password",
        response=MessageOut,
        throttle=[ResetRequestThrottle()],
        summary="Envia o link de redefinição de senha",
        description="A resposta é sempre a mesma, exista ou não uma conta ativa com o e-mail.",
    )
    def request_reset_link(self, payload: ForgotPasswordIn) -> MessageOut:
        send_reset_link(payload.email)
        return MessageOut(detail=FORGOT_PASSWORD_RESPONSE)

    @http_post(
        "/reset-password",
        response=MessageOut,
        summary="Define uma nova senha com o link recebido por e-mail",
    )
    def confirm_password_reset(self, payload: ResetPasswordIn) -> MessageOut:
        reset_password(payload.uid, payload.token, payload.password, payload.password_confirmation)
        return MessageOut(detail=PASSWORD_CHANGED_RESPONSE)
