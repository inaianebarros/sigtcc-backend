from django.db import connection
from ninja_extra import NinjaExtraAPI, api_controller, http_get

from apps.accounts.api import CatalogController, RegistrationController
from apps.accounts.password_reset_api import PasswordResetController
from apps.accounts.profile_api import StudentProfileController, TeacherProfileController
from apps.accounts.session_api import SessionController
from apps.advising.api import AdvisingRequestController
from config.errors import ConflictError, FieldValidationError

api = NinjaExtraAPI(
    title="SIGTCC API",
    description="API do Sistema de Gestão de Trabalhos de Conclusão de Curso da UFOPA.",
    version="0.1.0",
)


@api_controller("/", tags=["Sistema"])
class HealthController:
    @http_get("/health", summary="Verifica se a API e o banco de dados estão disponíveis")
    def health(self) -> dict[str, str]:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return {"status": "ok"}


@api.exception_handler(FieldValidationError)
def field_validation_error(request, exc: FieldValidationError):
    detail = [
        {"loc": ["body", "payload", field], "msg": message, "type": "value_error"}
        for field, message in exc.errors.items()
    ]
    return api.create_response(request, {"detail": detail}, status=422)


@api.exception_handler(ConflictError)
def conflict_error(request, exc: ConflictError):
    return api.create_response(request, {"detail": exc.detail}, status=409)


api.register_controllers(
    HealthController,
    CatalogController,
    RegistrationController,
    SessionController,
    PasswordResetController,
    StudentProfileController,
    TeacherProfileController,
    AdvisingRequestController,
)
