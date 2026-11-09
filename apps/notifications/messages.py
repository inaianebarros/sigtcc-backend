"""The Portuguese text of a notification, built from its type and its payload (never stored)."""

import datetime
from collections.abc import Callable

from .models import NotificationType

Payload = dict


def _date(iso: str) -> str:
    return datetime.date.fromisoformat(iso).strftime("%d/%m/%Y")


def _activity_created(payload: Payload) -> str:
    return (
        f'{payload["teacher_name"]} criou a atividade "{payload["activity_title"]}" para você, '
        f"com prazo em {_date(payload['deadline'])}."
    )


def _change_text(change: Payload) -> str:
    field, old, new = change["field"], change["old"], change["new"]
    if field == "title":
        return f'título de "{old}" para "{new}"'
    if field == "deadline":
        return f"prazo de {_date(old)} para {_date(new)}"
    # The description can be long: the payload keeps both texts, the message only says it changed.
    return "a descrição foi alterada"


def _activity_changed(payload: Payload) -> str:
    changes = "; ".join(_change_text(change) for change in payload["changes"])
    return (
        f'{payload["teacher_name"]} alterou a atividade "{payload["activity_title"]}": {changes}.'
    )


def _activity_deleted(payload: Payload) -> str:
    return f'{payload["teacher_name"]} excluiu a atividade "{payload["activity_title"]}".'


def _advising_ended(payload: Payload) -> str:
    archived, cancelled = payload["archived_activities"], payload["cancelled_meetings"]
    if archived == 0:
        activities = "Nenhuma atividade em aberto foi arquivada."
    elif archived == 1:
        activities = "1 atividade em aberto foi arquivada."
    else:
        activities = f"{archived} atividades em aberto foram arquivadas."
    if cancelled == 0:
        meetings = "Nenhuma reunião pendente ou aceita foi cancelada."
    elif cancelled == 1:
        meetings = "1 reunião pendente ou aceita foi cancelada."
    else:
        meetings = f"{cancelled} reuniões pendentes ou aceitas foram canceladas."
    return f"{payload['teacher_name']} encerrou a sua orientação. {activities} {meetings}"


def _when(iso: str) -> str:
    return datetime.datetime.fromisoformat(iso).strftime("%d/%m/%Y às %H:%M")


def _meeting_requested(payload: Payload) -> str:
    when = _when(payload["scheduled_at"])
    if payload["author_type"] == "teacher":
        return f"{payload['author_name']} marcou uma reunião com você para {when}."
    return f"{payload['author_name']} solicitou uma reunião para {when}."


def _meeting_answered(payload: Payload) -> str:
    accepted = payload["decision"] == "accepted"
    text = (
        f"{payload['responder_name']} {'aceitou' if accepted else 'recusou'} a reunião de "
        f"{_when(payload['scheduled_at'])}."
    )
    if not accepted and payload.get("reason"):
        text += f" Justificativa: {payload['reason']}"
    return text


def _advising_request_received(payload: Payload) -> str:
    return f"{payload['student_name']} enviou uma solicitação de orientação."


def _advising_request_answered(payload: Payload) -> str:
    verb = "aceitou" if payload["decision"] == "accepted" else "recusou"
    return f"{payload['teacher_name']} {verb} a sua solicitação de orientação."


_BUILDERS: dict[str, Callable[[Payload], str]] = {
    NotificationType.ADVISING_REQUEST_RECEIVED: _advising_request_received,
    NotificationType.ADVISING_REQUEST_ANSWERED: _advising_request_answered,
    NotificationType.ACTIVITY_CREATED: _activity_created,
    NotificationType.ACTIVITY_CHANGED: _activity_changed,
    NotificationType.ACTIVITY_DELETED: _activity_deleted,
    NotificationType.ADVISING_ENDED: _advising_ended,
    NotificationType.MEETING_REQUESTED: _meeting_requested,
    NotificationType.MEETING_ANSWERED: _meeting_answered,
}


def message_for(notification_type: str, payload: Payload) -> str:
    """The text to show. A type without a message of its own, or a payload from before the type
    carried the fields its message needs, falls back to the label of the type.
    """
    label = NotificationType(notification_type).label
    builder = _BUILDERS.get(notification_type)
    if builder is None:
        return label
    try:
        return builder(payload)
    except KeyError:
        return label
