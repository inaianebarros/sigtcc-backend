from django.http import HttpRequest
from ninja_extra import api_controller, http_get
from ninja_jwt.authentication import JWTAuth

from apps.accounts.permissions import IsStudent
from config.schemas import ErrorOut

from .schemas import StudentDashboardOut
from .student import student_dashboard


@api_controller("/painel", tags=["Painel"], auth=JWTAuth())
class StudentDashboardController:
    @http_get(
        "/aluno",
        response={200: StudentDashboardOut, 401: ErrorOut, 403: ErrorOut},
        permissions=[IsStudent],
        summary="A Dashboard do Aluno",
        description=(
            "Somente leitura (RF50). Com orientação ativa: o orientador, o tema do TCC (nulo se "
            "não cadastrado), os indicadores das atividades (total, por status, atrasadas e "
            "progress_percent, nulo quando não há atividades) e as próximas atividades. Só "
            "contam as atividades da orientação ativa que não foram arquivadas nem excluídas "
            "(RN35). O progresso é concluídas ÷ total × 100, truncado, então nunca é 100 com "
            "atividade aberta (RN36). Atrasada é o prazo anterior a hoje no servidor, com "
            "status diferente de concluída; o status não muda (RN37, RN24). As próximas "
            "atividades são as pendentes, em andamento ou em revisão, por prazo (as atrasadas "
            "primeiro), até 5, e has_more diz se há mais. Sem orientação ativa: "
            "has_active_advising é falso, last_request traz a última solicitação se ela estiver "
            "pendente, recusada ou não respondida, e has_ended_advisings diz se há histórico."
        ),
    )
    def student(self, request: HttpRequest):
        return student_dashboard(request.user.student)
