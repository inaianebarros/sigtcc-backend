from django.http import HttpRequest
from ninja.responses import Status
from ninja_extra import api_controller, http_get, http_post, paginate
from ninja_jwt.authentication import JWTAuth

from apps.accounts.permissions import IsStudent, IsTeacher
from config.pagination import BoundedLimitOffsetPagination
from config.schemas import ErrorOut, FieldErrorsOut, Page

from .models import AdvisingRequestStatus
from .schemas import (
    AdvisingRequestAnswerIn,
    AdvisingRequestAnswerOut,
    AdvisingRequestIn,
    AdvisingRequestItemOut,
    AdvisingRequestOut,
    EndAdvisingIn,
    EndedAdvisingOut,
    RequestStatusOut,
)
from .services import (
    answer_request,
    end_advising,
    list_received_requests,
    request_advising,
    student_request_status,
)


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

    @http_post(
        "/solicitacoes/{request_id}/responder",
        response={200: AdvisingRequestAnswerOut, 404: ErrorOut, 409: ErrorOut},
        permissions=[IsTeacher],
        summary="O Professor aceita ou recusa uma solicitação",
        description="A recusa não tem justificativa. Aceitar cria a orientação ativa.",
    )
    def answer(self, request: HttpRequest, request_id: int, payload: AdvisingRequestAnswerIn):
        return answer_request(request.user.teacher, request_id, payload.action)

    @http_get(
        "/minha-solicitacao",
        response=RequestStatusOut,
        permissions=[IsStudent],
        summary="Situação da solicitação e da orientação do Aluno",
        description=(
            "Devolve a solicitação mais recente (ou nada), a orientação ativa e se o Aluno "
            "pode solicitar de novo. A pendente com mais de 7 dias aparece como não respondida. "
            "O can_request é consultivo: a solicitação ainda pode ser recusada com 409."
        ),
    )
    def my_request(self, request: HttpRequest):
        return student_request_status(request.user.student)

    @http_post(
        "/{int:advising_id}/encerrar",
        response={
            200: EndedAdvisingOut,
            401: ErrorOut,
            403: ErrorOut,
            404: ErrorOut,
            409: ErrorOut,
            422: FieldErrorsOut,
        },
        permissions=[IsTeacher],
        summary="O Professor orientador encerra a orientação",
        description=(
            "Irreversível (RN30). Exige o motivo, de 10 a 500 caracteres sem contar os espaços "
            "nas pontas (RN33). Na mesma transação, arquiva as atividades pendentes, em andamento "
            "e em revisão que não foram excluídas (RN07, ADR 0004) e libera o Aluno para solicitar "
            "de novo. Quem não é o orientador recebe 404; orientação já encerrada recebe 409."
        ),
    )
    def end(self, request: HttpRequest, advising_id: int, payload: EndAdvisingIn):
        return end_advising(request.user.teacher, advising_id, payload.reason)
