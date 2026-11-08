from datetime import date, datetime
from typing import Annotated

from django.http import HttpRequest
from django.utils import timezone
from ninja import Schema
from ninja.responses import Status
from ninja_extra import api_controller, http_post
from ninja_jwt.authentication import JWTAuth
from pydantic import StringConstraints, field_validator

from apps.accounts.permissions import IsTeacher
from config.schemas import ErrorOut, FieldErrorsOut

from .models import Activity
from .services import create_activity

TITLE_MAX_LENGTH = 200
DESCRIPTION_MAX_LENGTH = 2000
PAST_DEADLINE_MESSAGE = "O prazo não pode ser anterior a hoje."

Title = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=TITLE_MAX_LENGTH),
]
Description = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=DESCRIPTION_MAX_LENGTH),
]


class ActivityIn(Schema):
    student_id: int
    title: Title
    description: Description
    deadline: date

    @field_validator("deadline")
    @classmethod
    def deadline_is_not_in_the_past(cls, value: date) -> date:
        if value < timezone.localdate():
            raise ValueError(PAST_DEADLINE_MESSAGE)
        return value


class ActivityOut(Schema):
    id: int
    title: str
    description: str
    deadline: date
    status: str
    created_at: datetime


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
