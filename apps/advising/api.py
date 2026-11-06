from django.http import HttpRequest
from ninja.responses import Status
from ninja_extra import api_controller, http_get, http_post, paginate
from ninja_jwt.authentication import JWTAuth

from apps.accounts.permissions import IsStudent, IsTeacher
from config.pagination import BoundedLimitOffsetPagination
from config.schemas import ErrorOut, Page

from .models import AdvisingRequestStatus
from .schemas import AdvisingRequestIn, AdvisingRequestItemOut, AdvisingRequestOut
from .services import list_received_requests, request_advising


@api_controller("/orientacao", tags=["Orientação"], auth=JWTAuth())
class AdvisingRequestController:
    @http_post(
        "/solicitacoes",
        response={201: AdvisingRequestOut, 404: ErrorOut, 409: ErrorOut},
        permissions=[IsStudent],
        summary="O Aluno solicita orientação a um Professor",
    )
    def create_request(self, request: HttpRequest, payload: AdvisingRequestIn):
        advising_request = request_advising(request.user.student, payload.teacher_id)
        return Status(201, AdvisingRequestOut.from_request(advising_request))

    @http_get(
        "/solicitacoes",
        response=Page[AdvisingRequestItemOut],
        permissions=[IsTeacher],
        summary="Lista as solicitações recebidas pelo Professor",
        description=(
            "Da mais recente para a mais antiga. A pendente com mais de 7 dias aparece como "
            "não respondida (ADR 0003)."
        ),
    )
    @paginate(BoundedLimitOffsetPagination)
    def list_requests(self, request: HttpRequest, status: AdvisingRequestStatus | None = None):
        return list_received_requests(request.user.teacher, status)
