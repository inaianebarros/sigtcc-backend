import datetime
from typing import Annotated

from django.http import HttpRequest
from django.utils import timezone
from ninja import Schema
from ninja.responses import Status
from ninja_extra import api_controller, http_post
from ninja_jwt.authentication import JWTAuth
from pydantic import StringConstraints

from apps.accounts.permissions import IsStudent, IsTeacher
from apps.advising.models import AnswerAction
from config.schemas import ErrorOut, FieldErrorsOut

from .models import Meeting
from .services import answer_meeting, create_meeting

DESCRIPTION_MAX_LENGTH = 500

MeetingDescription = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=DESCRIPTION_MAX_LENGTH),
]
REASON_MAX_LENGTH = 500

MeetingReason = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=REASON_MAX_LENGTH),
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

    @staticmethod
    def resolve_scheduled_at(obj: Meeting) -> datetime.datetime:
        return timezone.localtime(obj.scheduled_at)


class MeetingAnswerIn(Schema):
    action: AnswerAction
    reason: MeetingReason | None = None


class MeetingAnswerOut(MeetingOut):
    rejection_reason: str


@api_controller("/reunioes", tags=["Reuniões"], auth=JWTAuth())
class MeetingController:
    @http_post(
        "",
        response={
            201: MeetingOut,
            401: ErrorOut,
            403: ErrorOut,
            404: ErrorOut,
            409: ErrorOut,
            422: FieldErrorsOut,
        },
        permissions=[IsStudent | IsTeacher],
        summary="O Aluno ou o Professor solicita uma reunião",
        description=(
            "Data e hora no horário de Santarém, depois do momento atual, e uma descrição de 1 "
            "a 500 caracteres. A reunião nasce pendente, com quem a criou como autor. O Aluno "
            "solicita ao seu orientador ativo e não informa student_id; sem orientação ativa, "
            "409 (RN08, RN29). O Professor informa o student_id (o id do usuário do Aluno) de "
            "um orientando ativo; qualquer outro Aluno, e a orientação encerrada durante a "
            "ação, respondem o mesmo 404, sem revelar o Aluno."
        ),
    )
    def create(self, request: HttpRequest, payload: MeetingIn):
        meeting = create_meeting(
            request.user, payload.date, payload.time, payload.description, payload.student_id
        )
        return Status(201, meeting)

    @http_post(
        "/{int:meeting_id}/responder",
        response={
            200: MeetingAnswerOut,
            401: ErrorOut,
            403: ErrorOut,
            404: ErrorOut,
            409: ErrorOut,
            422: FieldErrorsOut,
        },
        permissions=[IsStudent | IsTeacher],
        summary="A outra parte da orientação aceita ou recusa uma solicitação de reunião",
        description=(
            "action é accept ou reject. A recusa exige reason, de 1 a 500 caracteres (RN11), e "
            "o aceite não o aceita. Só a outra parte responde: o autor, a reunião de outra "
            "orientação ou de orientação encerrada e a inexistente respondem o mesmo 404. A "
            "resposta é definitiva: reunião que não está pendente, ou cujo horário já passou, "
            "responde 409 e não muda."
        ),
    )
    def answer(self, request: HttpRequest, meeting_id: int, payload: MeetingAnswerIn):
        return answer_meeting(request.user, meeting_id, payload.action, payload.reason)
