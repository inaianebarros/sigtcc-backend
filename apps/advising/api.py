from typing import Annotated

from django.http import HttpRequest
from ninja import Query
from ninja.responses import Status
from ninja_extra import api_controller, http_get, http_post, paginate
from ninja_jwt.authentication import JWTAuth

from apps.accounts.permissions import IsStudent, IsTeacher
from config.pagination import BoundedLimitOffsetPagination
from config.schemas import ErrorOut, FieldErrorsOut, Page

from .calendar_advisees import list_calendar_advisees
from .models import AdvisingRequestStatus
from .schemas import (
    AdviseeOut,
    AdvisingHistorySummaryOut,
    AdvisingRequestAnswerIn,
    AdvisingRequestAnswerOut,
    AdvisingRequestIn,
    AdvisingRequestItemOut,
    AdvisingRequestOut,
    CalendarAdviseeOut,
    EndAdvisingIn,
    EndedAdvisingItemOut,
    EndedAdvisingOut,
    RequestStatusOut,
)
from .services import (
    advising_history_summary,
    answer_request,
    end_advising,
    list_advisees,
    list_ended_advisings,
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
            "e em revisão que não foram excluídas (RN07, ADR 0004), cancela as reuniões pendentes "
            "ou aceitas ainda não realizadas (RN34) e libera o Aluno para solicitar de novo. "
            "Quem não é o orientador recebe 404; orientação já encerrada recebe 409."
        ),
    )
    def end(self, request: HttpRequest, advising_id: int, payload: EndAdvisingIn):
        return end_advising(request.user.teacher, advising_id, payload.reason)

    @http_get(
        "/orientandos",
        response=Page[AdviseeOut],
        permissions=[IsTeacher],
        summary="Lista os Orientandos ativos do Professor",
        description=(
            "Só as orientações ativas do Professor, da que começou há menos tempo para a mais "
            "antiga. Cada item traz o Aluno e o curso, o início, o tema do TCC (ou nulo) e a "
            "contagem das atividades por status, sem as excluídas. Não traz e-mail nem matrícula. O "
            "parâmetro search filtra só pelo nome do Aluno, sem diferenciar maiúsculas e acentos, "
            "por texto contido; os espaços das pontas são removidos, vazio equivale a sem filtro "
            "e o máximo é de 100 caracteres. A ordem e a paginação não mudam, e o total reflete "
            "o filtro."
        ),
    )
    @paginate(BoundedLimitOffsetPagination)
    def advisees(
        self,
        request: HttpRequest,
        search: Annotated[str | None, Query(max_length=100)] = None,
    ):
        return list_advisees(request.user.teacher, search)

    @http_get(
        "/orientandos/calendario",
        response=Page[CalendarAdviseeOut],
        permissions=[IsTeacher],
        summary="Lista os orientandos do painel do calendário de reuniões",
        description=(
            "Os Alunos de todas as orientações do Professor, ativas ou encerradas (RF59): as "
            "reuniões das encerradas continuam no histórico (RF19). O Aluno que teve mais de uma "
            "orientação com o Professor aparece uma vez, e ended só é verdadeiro quando nenhuma "
            "delas está ativa. Cada item traz o id do usuário do Aluno, o nome e ended; não traz "
            "e-mail nem matrícula. Só Alunos vinculados ao Professor e com a conta ativa (RN32). "
            "O parâmetro search filtra pelo nome, sem diferenciar maiúsculas e acentos, por "
            "texto contido; os espaços das pontas são removidos, vazio equivale a sem filtro e o "
            "máximo é de 100 caracteres. Ordenado por nome, sem acento, e id; o total reflete o "
            "filtro. A listagem de /orientandos, só das orientações ativas, não muda."
        ),
    )
    @paginate(BoundedLimitOffsetPagination)
    def calendar_advisees(
        self,
        request: HttpRequest,
        search: Annotated[str | None, Query(max_length=100)] = None,
    ):
        return list_calendar_advisees(request.user.teacher, search)

    @http_get(
        "/historico",
        response=Page[EndedAdvisingItemOut],
        permissions=[IsStudent | IsTeacher],
        summary="O histórico de orientações encerradas do usuário",
        description=(
            "O Aluno vê as suas orientações encerradas e o Professor as que ele orientou, da "
            "que terminou há menos tempo para a mais antiga (RF49). Cada item traz a outra parte "
            "(id e nome), o início, o encerramento, o motivo (RN33) e o tema do TCC congelado "
            "(RN12), ou nulo se a orientação não teve tema. Só as orientações do próprio usuário: "
            "nada de outra pessoa é devolvido. A orientação ativa não aparece. O parâmetro search "
            "filtra pelo nome da outra parte ou pelo tema congelado, sem diferenciar maiúsculas e "
            "acentos, por texto contido; não olha o motivo nem as datas. Os espaços das pontas "
            "são removidos, vazio equivale a sem filtro e o máximo é de 200 caracteres. A ordem "
            "e a paginação não mudam, e o total reflete o filtro."
        ),
    )
    @paginate(BoundedLimitOffsetPagination)
    def history(
        self,
        request: HttpRequest,
        search: Annotated[str | None, Query(max_length=200)] = None,
    ):
        return list_ended_advisings(request.user, search)

    @http_get(
        "/historico/resumo",
        response=AdvisingHistorySummaryOut,
        permissions=[IsStudent | IsTeacher],
        summary="Os totais do topo do histórico de orientações",
        description=(
            "ended_count é o total de orientações encerradas do usuário, as mesmas do histórico, "
            "inclusive as que o sistema encerrou ao desativar uma conta (RF49); active_count é o "
            "total de orientações ativas do Professor e é nulo para o Aluno, que tem no máximo "
            "uma. Os dois números são gerais: não recebem busca nem filtro e só contam as "
            "orientações do próprio usuário."
        ),
    )
    def history_summary(self, request: HttpRequest):
        return advising_history_summary(request.user)
