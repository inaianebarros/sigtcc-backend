from datetime import datetime

from django.http import HttpRequest
from ninja import Schema
from ninja_extra import api_controller, http_get, http_patch, paginate
from ninja_jwt.authentication import JWTAuth

from apps.accounts.permissions import IsStudent, IsTeacher
from config.pagination import BoundedLimitOffsetPagination
from config.schemas import ErrorOut, FieldErrorsOut, Page

from .messages import message_for
from .models import Notification
from .queries import list_notifications, unread_count
from .services import mark_all_as_read, mark_as_read

LIST_DESCRIPTION = (
    "As notificações de quem consulta (cada usuário só vê as suas), da mais nova para a mais "
    "antiga, paginadas (limit e offset) e com o filtro opcional is_read. Cada item traz message, "
    "um texto pronto em português gerado no servidor a partir do tipo e do payload, e reference "
    "(type e id do objeto a que se refere, para o link, ou nulo). O payload é o retrato do evento "
    "no momento em que ocorreu, porque a atividade ou a reunião pode mudar depois. Estrutura do "
    "payload por tipo: "
    "advising_request_received: student_name. "
    "advising_request_answered: teacher_name e decision (accepted ou rejected). "
    "activity_created: activity_title, deadline (AAAA-MM-DD) e teacher_name. "
    "activity_changed: activity_title, teacher_name e changes, uma lista de field (title, "
    "description ou deadline), old e new, nessa ordem. "
    "activity_deleted: activity_title e teacher_name. "
    "advising_ended: teacher_name, archived_activities e cancelled_meetings. "
    "meeting_requested: author_name, author_type (student ou teacher), scheduled_at (com o "
    "fuso de Santarém) e description. "
    "meeting_answered: responder_name, decision (accepted ou rejected), reason (só na recusa) "
    "e scheduled_at. "
    "Os tipos sem aviso ainda (comentário, entrega e revisão de atividade, solicitação não "
    "respondida) têm payload vazio e message com o nome do tipo."
)


class NotificationReferenceOut(Schema):
    type: str
    id: int


class NotificationOut(Schema):
    id: int
    type: str
    message: str
    payload: dict
    reference: NotificationReferenceOut | None
    is_read: bool
    created_at: datetime

    @staticmethod
    def resolve_message(obj: Notification) -> str:
        return message_for(obj.type, obj.payload)

    @staticmethod
    def resolve_reference(obj: Notification) -> NotificationReferenceOut | None:
        if not obj.reference_type or obj.reference_id is None:
            return None
        return NotificationReferenceOut(type=obj.reference_type, id=obj.reference_id)


class CountOut(Schema):
    count: int


@api_controller("/notificacoes", tags=["Notificações"], auth=JWTAuth())
class NotificationController:
    @http_get(
        "",
        response={200: Page[NotificationOut], 401: ErrorOut, 403: ErrorOut, 422: FieldErrorsOut},
        permissions=[IsStudent | IsTeacher],
        summary="Lista as notificações de quem consulta",
        description=LIST_DESCRIPTION,
    )
    @paginate(BoundedLimitOffsetPagination)
    def list_mine(self, request: HttpRequest, is_read: bool | None = None):
        return list_notifications(request.user, is_read)

    @http_get(
        "/nao-lidas/contagem",
        response={200: CountOut, 401: ErrorOut, 403: ErrorOut},
        permissions=[IsStudent | IsTeacher],
        summary="Conta as notificações não lidas",
        description=(
            "Devolve só o total de notificações não lidas de quem consulta, para o selo do sino "
            "(consulta leve, repetida a cada 30 segundos, ADR 0003)."
        ),
    )
    def count_unread(self, request: HttpRequest):
        return {"count": unread_count(request.user)}

    @http_patch(
        "/todas-lidas",
        response={200: CountOut, 401: ErrorOut, 403: ErrorOut},
        permissions=[IsStudent | IsTeacher],
        summary="Marca todas as notificações como lidas",
        description=(
            "Marca como lidas todas as notificações não lidas de quem consulta e devolve quantas "
            "mudaram (zero se já estavam todas lidas)."
        ),
    )
    def read_all(self, request: HttpRequest):
        return {"count": mark_all_as_read(request.user)}

    @http_patch(
        "/{int:notification_id}/lida",
        response={200: NotificationOut, 401: ErrorOut, 403: ErrorOut, 404: ErrorOut},
        permissions=[IsStudent | IsTeacher],
        summary="Marca uma notificação como lida",
        description=(
            "Marca uma notificação de quem consulta como lida e a devolve. Repetir não muda "
            "nada (200). A notificação de outra pessoa ou inexistente responde o mesmo 404."
        ),
    )
    def read_one(self, request: HttpRequest, notification_id: int):
        return mark_as_read(request.user, notification_id)
