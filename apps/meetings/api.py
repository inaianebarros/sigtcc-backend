import datetime
from typing import Annotated

from django.http import HttpRequest
from ninja import Schema
from ninja.responses import Status
from ninja_extra import api_controller, http_post
from ninja_jwt.authentication import JWTAuth
from pydantic import StringConstraints

from apps.accounts.permissions import IsStudent
from config.schemas import ErrorOut, FieldErrorsOut

from .services import request_meeting_as_student

DESCRIPTION_MAX_LENGTH = 500

MeetingDescription = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=DESCRIPTION_MAX_LENGTH),
]


class MeetingIn(Schema):
    date: datetime.date
    time: datetime.time
    description: MeetingDescription
    student_id: int | None = None


class MeetingAuthorOut(Schema):
    id: int
    full_name: str
    type: str


class MeetingOut(Schema):
    id: int
    scheduled_at: datetime.datetime
    description: str
    status: str
    author: MeetingAuthorOut


@api_controller("/reunioes", tags=["Reuniões"], auth=JWTAuth())
class MeetingController:
    @http_post(
        "",
        response={
            201: MeetingOut,
            401: ErrorOut,
            403: ErrorOut,
            409: ErrorOut,
            422: FieldErrorsOut,
        },
        permissions=[IsStudent],
        summary="O Aluno solicita uma reunião ao seu orientador",
        description=(
            "Data e hora no horário de Santarém, depois do momento atual, e uma descrição de 1 "
            "a 500 caracteres. A reunião nasce pendente, com o Aluno como autor. Sem orientação "
            "ativa, 409 (RN08, RN29). O Aluno não informa student_id."
        ),
    )
    def create(self, request: HttpRequest, payload: MeetingIn):
        meeting = request_meeting_as_student(
            request.user.student,
            payload.date,
            payload.time,
            payload.description,
            payload.student_id,
        )
        return Status(201, meeting)
