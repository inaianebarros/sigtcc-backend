from django.http import HttpRequest
from ninja_extra import api_controller, http_get
from ninja_jwt.authentication import JWTAuth

from apps.accounts.permissions import IsStudent, IsTeacher
from apps.meetings.api import MeetingItemOut
from config.schemas import ErrorOut

from .report import report_meeting


@api_controller(
    "/orientacao/historico/{int:advising_id}/reunioes", tags=["Orientação"], auth=JWTAuth()
)
class ReportMeetingController:
    """A meeting read from the report of an ended advising (RF61, ADR 0026)."""

    @http_get(
        "/{int:meeting_id}",
        response={200: MeetingItemOut, 401: ErrorOut, 403: ErrorOut, 404: ErrorOut},
        permissions=[IsStudent | IsTeacher],
        summary="Detalhes de uma reunião do relatório de uma orientação encerrada",
        description=(
            "O mesmo item do calendário (GET /reunioes/calendario) para uma reunião realizada "
            "(RN43) do relatório (RF61): início, término, descrição, situação, autor, a outra "
            "parte e os apontamentos (RN44). Na orientação encerrada, todas as ações (can_*) são "
            "falsas. Só o Aluno e o Professor da orientação encerrada leem (RN47): quem não é "
            "parte, um id de orientação desconhecido, uma orientação ainda ativa, uma reunião de "
            "outra orientação ou que não foi realizada respondem o mesmo 404."
        ),
    )
    def detail(self, request: HttpRequest, advising_id: int, meeting_id: int):
        return report_meeting(request.user, advising_id, meeting_id)
