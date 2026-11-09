from datetime import date, datetime
from enum import StrEnum
from typing import Annotated

from django.http import HttpRequest
from django.utils import timezone
from ninja import Schema
from ninja.responses import Status
from ninja_extra import api_controller, http_delete, http_get, http_patch, http_post, paginate
from ninja_jwt.authentication import JWTAuth
from pydantic import StringConstraints, field_validator, model_validator

from apps.accounts.permissions import IsStudent, IsTeacher
from apps.advising.schemas import TeacherSummaryOut
from config.pagination import BoundedLimitOffsetPagination
from config.schemas import ErrorOut, FieldErrorsOut, Page, check_partial_update

from .models import Activity, ActivityStatus, Review, Submission, SubmissionFile
from .queries import activity_detail, list_activities
from .services import (
    EDITABLE_FIELDS,
    PAST_DEADLINE_MESSAGE,
    create_activity,
    delete_activity,
    edit_activity,
)

TITLE_MAX_LENGTH = 200
DESCRIPTION_MAX_LENGTH = 2000

ActivityTitle = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=TITLE_MAX_LENGTH),
]
ActivityDescription = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=DESCRIPTION_MAX_LENGTH),
]


class ActivityIn(Schema):
    student_id: int
    title: ActivityTitle
    description: ActivityDescription
    deadline: date

    @field_validator("deadline")
    @classmethod
    def deadline_is_not_in_the_past(cls, value: date) -> date:
        if value < timezone.localdate():
            raise ValueError(PAST_DEADLINE_MESSAGE)
        return value


class ActivityPatchIn(Schema):
    title: ActivityTitle | None = None
    description: ActivityDescription | None = None
    deadline: date | None = None

    @model_validator(mode="after")
    def at_least_one_field_and_no_nulls(self):
        check_partial_update(self, EDITABLE_FIELDS)
        return self


class ActivityOut(Schema):
    id: int
    title: str
    description: str
    deadline: date
    status: str
    created_at: datetime


ListedStatus = StrEnum(
    "ListedStatus",
    {status.name: status.value for status in ActivityStatus if status != ActivityStatus.ARCHIVED},
)


class ActivityStudentOut(Schema):
    id: int
    full_name: str


class ActivityItemOut(Schema):
    id: int
    title: str
    deadline: date
    status: str
    overdue: bool
    student: ActivityStudentOut

    @staticmethod
    def resolve_student(obj: Activity) -> ActivityStudentOut:
        user = obj.advising.student.user
        return ActivityStudentOut(id=user.id, full_name=user.full_name)


class SubmissionFileOut(Schema):
    id: int
    file_name: str
    size_bytes: int
    content_type: str


class SubmissionOut(Schema):
    id: int
    submitted_at: datetime
    files: list[SubmissionFileOut]

    @staticmethod
    def resolve_files(obj: Submission) -> list[SubmissionFile]:
        return list(obj.files.all())


class ReviewOut(Schema):
    id: int
    decision: str
    decided_at: datetime


class ActivityDetailOut(ActivityItemOut):
    teacher: TeacherSummaryOut
    description: str
    created_at: datetime
    submissions: list[SubmissionOut]
    reviews: list[ReviewOut]
    comments_count: int

    @staticmethod
    def resolve_teacher(obj: Activity) -> TeacherSummaryOut:
        return TeacherSummaryOut.from_teacher(obj.advising.teacher)

    @staticmethod
    def resolve_submissions(obj: Activity) -> list[Submission]:
        return obj.ordered_submissions

    @staticmethod
    def resolve_reviews(obj: Activity) -> list[Review]:
        return obj.ordered_reviews


@api_controller("/atividades", tags=["Atividades"], auth=JWTAuth())
class ActivityController:
    @http_post(
        "",
        response={
            201: ActivityOut,
            401: ErrorOut,
            403: ErrorOut,
            404: ErrorOut,
            422: FieldErrorsOut,
        },
        permissions=[IsTeacher],
        summary="O Professor cria uma atividade para um orientando",
        description=(
            "A atividade nasce pendente e pertence à orientação ativa do Aluno. O prazo é uma "
            "data de hoje em diante (data do servidor). Sem orientação ativa com o Aluno (sem "
            "vínculo, só encerrada ou de outro Professor) e id desconhecido respondem o mesmo "
            "404, sem revelar o Aluno (RN13)."
        ),
    )
    def create(self, request: HttpRequest, payload: ActivityIn):
        activity: Activity = create_activity(
            request.user.teacher,
            payload.student_id,
            payload.title,
            payload.description,
            payload.deadline,
        )
        return Status(201, activity)

    @http_get(
        "",
        response={200: Page[ActivityItemOut], 401: ErrorOut, 403: ErrorOut, 422: FieldErrorsOut},
        permissions=[IsStudent | IsTeacher],
        summary="Lista as atividades do Aluno ou dos orientandos do Professor",
        description=(
            "O Aluno vê as da própria orientação ativa e o Professor as de todos os orientandos "
            "ativos. Excluídas, arquivadas e de orientações encerradas não aparecem. Filtros "
            "combinados por E: status (pending, in_progress, in_review ou completed), overdue "
            "(true só as atrasadas, false só as não atrasadas) e student_id, dentro do escopo de "
            "quem consulta. As abertas vêm primeiro, pelo prazo mais próximo, e depois as "
            "concluídas, do prazo mais recente para o mais antigo (RN24). Paginada com limit "
            "(padrão 20, máximo 100) e offset."
        ),
    )
    @paginate(BoundedLimitOffsetPagination)
    def list_my_activities(
        self,
        request: HttpRequest,
        status: ListedStatus | None = None,
        overdue: bool | None = None,
        student_id: int | None = None,
    ):
        return list_activities(request.user, status, overdue, student_id)

    @http_get(
        "/{int:activity_id}",
        response={200: ActivityDetailOut, 401: ErrorOut, 403: ErrorOut, 404: ErrorOut},
        permissions=[IsStudent | IsTeacher],
        summary="Detalhes de uma atividade",
        description=(
            "Os campos da lista mais a descrição, a data de criação, as entregas (com nome, "
            "tamanho e tipo dos arquivos, sem endereço de download), as revisões e a contagem "
            "de comentários. Atividade fora do escopo de quem consulta, excluída, arquivada, de "
            "orientação encerrada ou inexistente responde o mesmo 404."
        ),
    )
    def read_activity(self, request: HttpRequest, activity_id: int):
        return activity_detail(request.user, activity_id)

    @http_patch(
        "/{int:activity_id}",
        response={
            200: ActivityOut,
            401: ErrorOut,
            403: ErrorOut,
            404: ErrorOut,
            409: ErrorOut,
            422: FieldErrorsOut,
        },
        permissions=[IsTeacher],
        summary="O Professor orientador edita uma atividade",
        description=(
            "Edição parcial de title, description e deadline, com ao menos um campo e os mesmos "
            "limites da criação. Só um prazo que mudou precisa ser de hoje em diante, então o "
            "título de uma atividade atrasada pode ser corrigido. Concluída responde 409 (RN15). "
            "Outro Professor, atividade excluída, inexistente ou de orientação encerrada "
            "respondem o mesmo 404 (RN14)."
        ),
    )
    def edit(self, request: HttpRequest, activity_id: int, payload: ActivityPatchIn):
        result = edit_activity(
            request.user.teacher,
            activity_id,
            title=payload.title,
            description=payload.description,
            deadline=payload.deadline,
        )
        return result.activity

    @http_delete(
        "/{int:activity_id}",
        response={204: None, 401: ErrorOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut},
        permissions=[IsTeacher],
        summary="O Professor orientador exclui uma atividade",
        description=(
            "Exclusão lógica: a atividade some da lista e do detalhe, e as entregas, revisões e "
            "comentários são preservados. Não há como desfazer. Concluída responde 409 (RN15). "
            "Outro Professor, atividade já excluída, inexistente ou de orientação encerrada "
            "respondem o mesmo 404 (RN14)."
        ),
    )
    def remove(self, request: HttpRequest, activity_id: int):
        delete_activity(request.user.teacher, activity_id)
        return Status(204, None)
