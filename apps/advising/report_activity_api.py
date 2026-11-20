from typing import Annotated

from django.http import HttpRequest
from ninja import Query
from ninja.conf import settings as ninja_settings
from ninja_extra import api_controller, http_get
from ninja_jwt.authentication import JWTAuth

from apps.accounts.permissions import IsStudent, IsTeacher
from apps.activities.api import FILE_DOWNLOAD_OPENAPI, ActivityDetailOut, attachment_response
from apps.activities.queries import activity_detail
from apps.activities.services import open_review_file, open_submission_file
from apps.activities.timeline import DEFAULT_PAGE_SIZE, activity_timeline
from apps.activities.timeline_schemas import TimelineOut
from config.schemas import ErrorOut, FieldErrorsOut

from .report import report_activity_scope

ACCESS = (
    "Só o Aluno e o Professor da orientação encerrada leem (RN47): quem não é parte, um id de "
    "orientação desconhecido, uma orientação ainda ativa, uma atividade de outra orientação, "
    "excluída ou que não está no relatório respondem o mesmo 404."
)


@api_controller(
    "/orientacao/historico/{int:advising_id}/atividades/{int:activity_id}",
    tags=["Orientação"],
    auth=JWTAuth(),
)
class ReportActivityController:
    """An activity read from the report of an ended advising (RF61, ADR 0026): what the detail
    of an active one shows, with nothing to act on."""

    @http_get(
        "",
        response={200: ActivityDetailOut, 401: ErrorOut, 403: ErrorOut, 404: ErrorOut},
        permissions=[IsStudent | IsTeacher],
        summary="Detalhes de uma atividade do relatório de uma orientação encerrada",
        description=(
            "O mesmo conteúdo do detalhe da atividade (GET /atividades/{id}) para uma atividade "
            "concluída ou arquivada do relatório (RF61). " + ACCESS
        ),
    )
    def detail(self, request: HttpRequest, advising_id: int, activity_id: int):
        return activity_detail(report_activity_scope(request.user, advising_id), activity_id)

    @http_get(
        "/linha-do-tempo",
        response={
            200: TimelineOut,
            401: ErrorOut,
            403: ErrorOut,
            404: ErrorOut,
            422: FieldErrorsOut,
        },
        permissions=[IsStudent | IsTeacher],
        summary="Lê a linha do tempo de uma atividade do relatório de uma orientação encerrada",
        description=(
            "A mesma linha do tempo de GET /atividades/{id}/linha-do-tempo (RN49), com os mesmos "
            "parâmetros before e limit, para uma atividade do relatório (RF61). Os arquivos "
            "baixam pelos downloads desta mesma atividade do relatório. " + ACCESS
        ),
    )
    def timeline(
        self,
        request: HttpRequest,
        advising_id: int,
        activity_id: int,
        before: Annotated[int | None, Query(ge=1)] = None,
        limit: Annotated[
            int, Query(ge=1, le=ninja_settings.PAGINATION_MAX_LIMIT)
        ] = DEFAULT_PAGE_SIZE,
    ):
        scope = report_activity_scope(request.user, advising_id)
        return activity_timeline(scope, activity_id, before, limit)

    @http_get(
        "/arquivos/{int:file_id}",
        response={401: ErrorOut, 403: ErrorOut, 404: ErrorOut, 503: ErrorOut},
        permissions=[IsStudent | IsTeacher],
        summary="Baixa um arquivo de entrega de uma atividade do relatório",
        description=(
            "O mesmo download de GET /atividades/{id}/arquivos/{file_id} para uma atividade do "
            "relatório (RF61). Arquivo inexistente ou de outra atividade também responde 404, e "
            "o armazenamento fora do ar, 503. " + ACCESS
        ),
        openapi_extra=FILE_DOWNLOAD_OPENAPI,
    )
    def download(self, request: HttpRequest, advising_id: int, activity_id: int, file_id: int):
        scope = report_activity_scope(request.user, advising_id)
        submission_file, content = open_submission_file(scope, activity_id, file_id)
        return attachment_response(submission_file, content)

    @http_get(
        "/revisoes/{int:review_id}/arquivos/{int:file_id}",
        response={401: ErrorOut, 403: ErrorOut, 404: ErrorOut, 503: ErrorOut},
        permissions=[IsStudent | IsTeacher],
        summary="Baixa um anexo de uma recusa de uma atividade do relatório",
        description=(
            "O mesmo download de GET /atividades/{id}/revisoes/{review_id}/arquivos/{file_id} "
            "para uma atividade do relatório (RF61, RN48). Anexo inexistente, de outra decisão "
            "ou de outra atividade também responde 404, e o armazenamento fora do ar, 503. "
            + ACCESS
        ),
        openapi_extra=FILE_DOWNLOAD_OPENAPI,
    )
    def download_review_file(
        self,
        request: HttpRequest,
        advising_id: int,
        activity_id: int,
        review_id: int,
        file_id: int,
    ):
        scope = report_activity_scope(request.user, advising_id)
        review_file, content = open_review_file(scope, activity_id, review_id, file_id)
        return attachment_response(review_file, content)
