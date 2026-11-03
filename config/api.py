from django.db import connection
from ninja_extra import NinjaExtraAPI, api_controller, http_get

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


api.register_controllers(HealthController)
