import datetime
from typing import Annotated

from django.http import HttpRequest
from django.utils import timezone
from ninja import Schema
from ninja.responses import Status
from ninja_extra import api_controller, http_get, http_patch, http_post, paginate
from ninja_jwt.authentication import JWTAuth
from pydantic import StringConstraints, model_validator

from apps.accounts.permissions import IsStudent, IsTeacher
from apps.advising.models import AdvisingStatus, AnswerAction
from config.pagination import BoundedLimitOffsetPagination
from config.schemas import ErrorOut, FieldErrorsOut, Page, check_partial_update

from .models import Meeting
from .queries import list_meetings
from .services import answer_meeting, cancel_meeting, create_meeting, edit_meeting
from .situations import MeetingSituation, can_answer, can_cancel, can_edit

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


class MeetingPatchIn(Schema):
    date: datetime.date | None = None
    time: datetime.time | None = None
    description: MeetingDescription | None = None

    @model_validator(mode="after")
    def at_least_one_field_and_no_nulls(self):
        check_partial_update(self, ("date", "time", "description"))
        return self


class MeetingCancelIn(Schema):
    reason: MeetingReason


class MeetingCancelOut(MeetingOut):
    cancellation_reason: str
    cancelled_by_system: bool


class MeetingCounterpartOut(Schema):
    id: int
    full_name: str


class MeetingItemOut(MeetingOut):
    situation: str
    counterpart: MeetingCounterpartOut
    rejection_reason: str
    cancellation_reason: str
    cancelled_by_system: bool
    can_answer: bool
    can_edit: bool
    can_cancel: bool

    @staticmethod
    def resolve_counterpart(obj: Meeting, context) -> MeetingCounterpartOut:
        advising = obj.advising
        user = context["request"].user
        other = advising.teacher if advising.student.user_id == user.id else advising.student
        return MeetingCounterpartOut(id=other.user_id, full_name=other.user.full_name)

    @staticmethod
    def resolve_can_answer(obj: Meeting, context) -> bool:
        return can_answer(*_action_inputs(obj, context))

    @staticmethod
    def resolve_can_edit(obj: Meeting, context) -> bool:
        return can_edit(*_action_inputs(obj, context))

    @staticmethod
    def resolve_can_cancel(obj: Meeting, context) -> bool:
        return can_cancel(*_action_inputs(obj, context))


def _action_inputs(obj: Meeting, context) -> tuple[str, bool, bool]:
    user = context["request"].user
    return obj.situation, obj.author_id == user.id, obj.advising.status == AdvisingStatus.ACTIVE


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

    @http_patch(
        "/{int:meeting_id}",
        response={
            200: MeetingOut,
            401: ErrorOut,
            403: ErrorOut,
            404: ErrorOut,
            409: ErrorOut,
            422: FieldErrorsOut,
        },
        permissions=[IsStudent | IsTeacher],
        summary="O autor edita uma reunião",
        description=(
            "Edição parcial de date, time e description, com ao menos um campo e os mesmos "
            "limites da criação. Só o autor edita (RN09); a outra parte, outra orientação, "
            "orientação encerrada e reunião inexistente respondem o mesmo 404. Recusada, "
            "cancelada ou com horário já passado responde 409. Um date ou time novo precisa ser "
            "futuro, e mudá-lo numa reunião aceita a devolve para pendente (RN39)."
        ),
    )
    def edit(self, request: HttpRequest, meeting_id: int, payload: MeetingPatchIn):
        return edit_meeting(
            request.user, meeting_id, payload.date, payload.time, payload.description
        ).meeting

    @http_post(
        "/{int:meeting_id}/cancelar",
        response={
            200: MeetingCancelOut,
            401: ErrorOut,
            403: ErrorOut,
            404: ErrorOut,
            409: ErrorOut,
            422: FieldErrorsOut,
        },
        permissions=[IsStudent | IsTeacher],
        summary="Cancela uma reunião, com motivo",
        description=(
            "O motivo é obrigatório, de 1 a 500 caracteres (RN10). A reunião pendente só o "
            "autor cancela, e a aceita qualquer das partes da orientação, enquanto o horário não "
            "passou. Recusada, já cancelada ou com horário passado responde 409. O cancelamento "
            "é definitivo."
        ),
    )
    def cancel(self, request: HttpRequest, meeting_id: int, payload: MeetingCancelIn):
        return cancel_meeting(request.user, meeting_id, payload.reason)

    @http_get(
        "",
        response={
            200: Page[MeetingItemOut],
            401: ErrorOut,
            403: ErrorOut,
            422: FieldErrorsOut,
        },
        permissions=[IsStudent | IsTeacher],
        summary="Lista as reuniões do Aluno ou do Professor",
        description=(
            "As reuniões de todas as orientações do usuário, ativas ou encerradas (RF19). A "
            "situação é derivada do status e do horário: pending, accepted, rejected, cancelled, "
            "held (aceita com horário passado) e expired (pendente com horário passado). Filtros "
            "por E: situation, date_from e date_to (datas inclusivas em Santarém) e, para o "
            "Professor, student_id. As futuras vêm primeiro, da mais próxima para a mais "
            "distante, e depois as passadas, da mais recente para a mais antiga. can_answer, "
            "can_edit e can_cancel dizem o que o usuário pode fazer agora (RN09, RN10, RN39)."
        ),
    )
    @paginate(BoundedLimitOffsetPagination)
    def list_my_meetings(
        self,
        request: HttpRequest,
        situation: MeetingSituation | None = None,
        date_from: datetime.date | None = None,
        date_to: datetime.date | None = None,
        student_id: int | None = None,
    ):
        return list_meetings(request.user, situation, date_from, date_to, student_id)
