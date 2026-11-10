from django.http import HttpRequest, HttpResponse
from ninja.responses import Status
from ninja_extra import api_controller, http_post
from ninja_jwt.authentication import JWTAuth

from config.errors import FieldValidationError
from config.rate_limit import enforce_ip_limit
from config.schemas import ErrorOut, FieldErrorsOut

from .failure_limit import clear_failures, ensure_not_blocked, record_failure
from .password_change import change_password
from .schemas import AccessTokenOut, ChangePasswordIn
from .sessions import ensure_trusted_origin

CHANGE_PASSWORD_FAILURES = "change_password_failures"


@api_controller("/auth", tags=["Autenticação"], auth=JWTAuth())
class PasswordChangeController:
    @http_post(
        "/change-password",
        response={
            200: AccessTokenOut,
            401: ErrorOut,
            403: ErrorOut,
            422: FieldErrorsOut,
            429: ErrorOut,
        },
        summary="Altera a senha do usuário autenticado",
        description=(
            "Exige a senha atual. Encerra as outras sessões do usuário (RN27) e abre uma nova "
            "para este dispositivo. Os erros de validação (422) vêm por campo e todos de uma vez; "
            "só a senha atual errada conta para o limite de tentativas por conta e IP (429). Cada "
            "requisição também conta para o limite por IP, seja qual for a conta (429 com Retry-After)."
        ),
    )
    def change(self, request: HttpRequest, payload: ChangePasswordIn, response: HttpResponse):
        enforce_ip_limit("change_password_ip", request)
        ensure_trusted_origin(request)
        user = request.user
        account = str(user.id)
        ensure_not_blocked(CHANGE_PASSWORD_FAILURES, account, request)
        try:
            access_token = change_password(
                user,
                payload.current_password,
                payload.password,
                payload.password_confirmation,
                response,
            )
        except FieldValidationError as error:
            if "current_password" in error.errors:
                record_failure(CHANGE_PASSWORD_FAILURES, account, request, user.id)
            raise
        clear_failures(CHANGE_PASSWORD_FAILURES, account, request)
        return Status(200, AccessTokenOut(access_token=access_token))
