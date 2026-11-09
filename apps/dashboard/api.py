from django.http import HttpRequest
from ninja_extra import api_controller, http_get
from ninja_jwt.authentication import JWTAuth

from apps.accounts.permissions import IsStudent, IsTeacher
from config.schemas import ErrorOut

from .schemas import StudentDashboardOut, TeacherDashboardOut
from .student import student_dashboard
from .teacher import teacher_dashboard


@api_controller("/painel", tags=["Painel"], auth=JWTAuth())
class DashboardController:
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

    @http_get(
        "/professor",
        response={200: TeacherDashboardOut, 401: ErrorOut, 403: ErrorOut},
        permissions=[IsTeacher],
        summary="A Dashboard do Professor",
        description=(
            "Somente leitura (RF51). Soma só as orientações ativas do Professor e as atividades "
            "que não foram arquivadas nem excluídas (RN35): advisees_count, activities (total, "
            "por status, atrasadas e progress_percent, truncado e nulo sem atividades, RN36), "
            "attention_advisees e next_deadlines. Os orientandos de atenção (RN38) têm ao menos "
            "uma atividade atrasada ou em revisão e vêm por mais atrasadas, depois mais em "
            "revisão e, por fim, pelo nome sem diferenciar maiúsculas; até 5, com has_more, e "
            "com o tema do TCC quando cadastrado. A classificação é informativa. Os próximos "
            "prazos são as atividades pendentes, em andamento ou em revisão, por prazo (as "
            "atrasadas primeiro), até 5, com has_more, o Aluno e a indicação de atraso (RN37). "
            "Sem orientação ativa, a resposta é vazia: advisees_count 0 e o resto nulo."
        ),
    )
    def teacher(self, request: HttpRequest):
        return teacher_dashboard(request.user.teacher)
