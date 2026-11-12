import datetime
from typing import Annotated

from django.http import HttpRequest
from django.utils import timezone
from ninja import Query, Schema
from ninja.responses import Status
from ninja_extra import api_controller, http_get, http_patch, http_post, http_put, paginate
from ninja_jwt.authentication import JWTAuth
from pydantic import StringConstraints, model_validator

from apps.accounts.permissions import IsStudent, IsTeacher
from apps.advising.models import AdvisingStatus, AnswerAction
from config.pagination import BoundedLimitOffsetPagination
from config.schemas import ErrorOut, FieldErrorsOut, Page, check_partial_update

from .models import Meeting
from .queries import list_meetings, meetings_in_period
from .services import (
    answer_meeting,
    cancel_meeting,
    create_meeting,
    edit_meeting,
    mark_meeting_held,
    write_meeting_notes,
)
from .situations import (
    MeetingSituation,
    can_answer,
    can_cancel,
    can_edit,
    can_edit_notes,
    can_mark_held,
)

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
    end_time: datetime.time
    description: MeetingDescription
    student_id: int | None = None


class MeetingAuthorOut(Schema):
    id: int
    full_name: str
    type: str


class MeetingOut(Schema):
    id: int
    scheduled_at: datetime.datetime
    ends_at: datetime.datetime
    description: str
    status: str
    author: MeetingAuthorOut

    @staticmethod
    def resolve_scheduled_at(obj: Meeting) -> datetime.datetime:
        return timezone.localtime(obj.scheduled_at)

    @staticmethod
    def resolve_ends_at(obj: Meeting) -> datetime.datetime:
        return timezone.localtime(obj.ends_at)


class MeetingPatchIn(Schema):
    date: datetime.date | None = None
    time: datetime.time | None = None
    end_time: datetime.time | None = None
    description: MeetingDescription | None = None

    @model_validator(mode="after")
    def at_least_one_field_and_no_nulls(self):
        check_partial_update(self, ("date", "time", "end_time", "description"))
        return self


class MeetingCancelIn(Schema):
    reason: MeetingReason


class MeetingCancelOut(MeetingOut):
    cancellation_reason: str
    cancelled_by_system: bool


NOTES_MAX_LENGTH = 3000

MeetingNotes = Annotated[str, StringConstraints(strip_whitespace=True, max_length=NOTES_MAX_LENGTH)]


class MeetingNotesIn(Schema):
    notes: MeetingNotes


class MeetingNotesOut(MeetingOut):
    notes: str


class MeetingHeldOut(MeetingOut):
    held_at: datetime.datetime

    @staticmethod
    def resolve_held_at(obj: Meeting) -> datetime.datetime:
        return timezone.localtime(obj.held_at)


class MeetingCounterpartOut(Schema):
    id: int
    full_name: str


class MeetingItemOut(MeetingOut):
    situation: MeetingSituation
    counterpart: MeetingCounterpartOut
    rejection_reason: str
    cancellation_reason: str
    cancelled_by_system: bool
    can_answer: bool
    can_edit: bool
    can_cancel: bool
    marked_held: bool
    can_mark_held: bool
    notes: str
    can_edit_notes: bool

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

    @staticmethod
    def resolve_marked_held(obj: Meeting) -> bool:
        return obj.held_at is not None

    @staticmethod
    def resolve_can_edit_notes(obj: Meeting, context) -> bool:
        user = context["request"].user
        is_advisor = obj.advising.teacher.user_id == user.id
        return can_edit_notes(
            obj.situation, is_advisor, obj.advising.status == AdvisingStatus.ACTIVE
        )

    @staticmethod
    def resolve_can_mark_held(obj: Meeting, context) -> bool:
        user = context["request"].user
        is_advisor = obj.advising.teacher.user_id == user.id
        return can_mark_held(
            obj.situation, is_advisor, obj.advising.status == AdvisingStatus.ACTIVE
        )


def _action_inputs(obj: Meeting, context) -> tuple[str, bool, bool]:
    user = context["request"].user
    return obj.situation, obj.author_id == user.id, obj.advising.status == AdvisingStatus.ACTIVE


class MeetingAnswerIn(Schema):
    action: AnswerAction
    reason: MeetingReason | None = None


class MeetingAnswerOut(MeetingOut):
    rejection_reason: str


class CalendarFilters(Schema):
    date_from: datetime.date
    date_to: datetime.date
    situation: list[MeetingSituation] | None = None
    hidden_student_ids: list[int] | None = None


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
            "Data, hora de início (time) e hora de término (end_time) no horário de Santarém: o "
            "início depois do momento atual e o término depois do início, no mesmo dia (RN45). "
            "A descrição tem de 1 a 500 caracteres. A reunião nasce pendente, com quem a criou como autor. O Aluno "
            "solicita ao seu orientador ativo e não informa student_id; sem orientação ativa, "
            "409 (RN08, RN29). O Professor informa o student_id (o id do usuário do Aluno) de "
            "um orientando ativo; qualquer outro Aluno, e a orientação encerrada durante a "
            "ação, respondem o mesmo 404, sem revelar o Aluno."
        ),
    )
    def create(self, request: HttpRequest, payload: MeetingIn):
        meeting = create_meeting(
            request.user,
            payload.date,
            payload.time,
            payload.end_time,
            payload.description,
            payload.student_id,
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
            "Edição parcial de date, time, end_time e description, com ao menos um campo e os "
            "mesmos limites da criação (o término continua depois do início, no mesmo dia). Só o autor edita (RN09); a outra parte, outra orientação, "
            "orientação encerrada e reunião inexistente respondem o mesmo 404. Recusada, "
            "cancelada ou com horário já passado responde 409. Um date ou time novo precisa ser "
            "futuro, e mudar date, time ou end_time numa reunião aceita a devolve para pendente (RN39)."
        ),
    )
    def edit(self, request: HttpRequest, meeting_id: int, payload: MeetingPatchIn):
        return edit_meeting(
            request.user,
            meeting_id,
            payload.date,
            payload.time,
            payload.end_time,
            payload.description,
        ).meeting

    @http_post(
        "/{int:meeting_id}/realizada",
        response={
            200: MeetingHeldOut,
            401: ErrorOut,
            403: ErrorOut,
            404: ErrorOut,
            409: ErrorOut,
        },
        permissions=[IsTeacher],
        summary="O Professor orientador marca uma reunião aceita como realizada",
        description=(
            "Só o Professor orientador, com a orientação ativa, marca uma reunião aceita cujo "
            "horário ainda não passou (RF57, RN43, ADR 0014). A marcação grava o momento e o "
            "autor e é irreversível. Reunião pendente, recusada, cancelada, já marcada ou com "
            "horário passado responde 409 e não muda. O Aluno responde 403, e outro Professor, a "
            "orientação encerrada e a reunião inexistente respondem o mesmo 404. Depois de "
            "marcada, a reunião não pode ser editada nem cancelada, sai das próximas reuniões dos "
            "dashboards e não é cancelada ao encerrar a orientação nem ao desativar o usuário. "
            "Não envia notificação."
        ),
    )
    def mark_held(self, request: HttpRequest, meeting_id: int):
        return mark_meeting_held(request.user, meeting_id)

    @http_put(
        "/{int:meeting_id}/apontamentos",
        response={
            200: MeetingNotesOut,
            401: ErrorOut,
            403: ErrorOut,
            404: ErrorOut,
            409: ErrorOut,
            422: FieldErrorsOut,
        },
        permissions=[IsTeacher],
        summary="O Professor orientador grava os apontamentos de uma reunião realizada",
        description=(
            "Substitui os apontamentos (RF58, RN44, ADR 0014): texto livre e opcional de até "
            "3000 caracteres, sem contar os espaços das pontas; um texto vazio ou só de espaços "
            "remove os apontamentos. Só o Professor orientador, com a orientação ativa, e só em "
            "reunião realizada, marcada por ele (RN43) ou aceita com horário passado: pendente, "
            "recusada, cancelada ou aceita futura não marcada responde 409. O Aluno responde 403, "
            "e outro Professor, a orientação encerrada (apontamentos congelados) e a reunião "
            "inexistente respondem o mesmo 404. O Aluno orientando lê os apontamentos na "
            "listagem. Não envia notificação."
        ),
    )
    def write_notes(self, request: HttpRequest, meeting_id: int, payload: MeetingNotesIn):
        return write_meeting_notes(request.user, meeting_id, payload.notes)

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
            "Não usada pela tela desde o calendário (GET /reunioes/calendario, ADR 0015); fica "
            "até ser removida. "
            "As reuniões de todas as orientações do usuário, ativas ou encerradas (RF19). A "
            "situação é derivada do status e do horário: pending, accepted, rejected, cancelled, "
            "held (aceita com horário passado ou marcada como realizada, RN43) e expired (pendente com horário passado). Filtros "
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

    @http_get(
        "/calendario",
        response={
            200: list[MeetingItemOut],
            401: ErrorOut,
            403: ErrorOut,
            422: FieldErrorsOut,
        },
        permissions=[IsStudent | IsTeacher],
        summary="Lê as reuniões do Aluno ou do Professor em um período, para o calendário",
        description=(
            "Todas as reuniões das orientações do usuário, ativas ou encerradas, entre date_from "
            "e date_to (obrigatórias, inclusivas, no horário de Santarém), sem paginar e "
            "ordenadas pelo início e pelo id (RF19, ADR 0015). O período tem no máximo 42 dias; "
            "acima disso, ou com date_to antes de date_from, responde 422. situation aceita uma "
            "ou várias situações: pending, accepted, rejected, cancelled, held (inclui a marcada como realizada, RN43) e expired. O "
            "Professor oculta orientandos com hidden_student_ids (ids do usuário do aluno); sem "
            "ela todos aparecem, inclusive os novos. O Aluno não oculta alunos e recebe 422. "
            "Cada item traz início, término, situação e o que o usuário pode fazer "
            "(can_answer, can_edit, can_cancel)."
        ),
    )
    def calendar(
        self,
        request: HttpRequest,
        filters: Query[CalendarFilters],
    ):
        return meetings_in_period(
            request.user,
            filters.date_from,
            filters.date_to,
            filters.situation,
            filters.hidden_student_ids,
        )
