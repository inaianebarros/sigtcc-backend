"""The Portuguese text of a notification, built from its type and its payload (never stored)."""

import datetime
from collections.abc import Callable

from .models import NotificationType

Payload = dict


def _format_date(iso: str) -> str:
    return datetime.date.fromisoformat(iso).strftime("%d/%m/%Y")


def _activity_created(payload: Payload) -> str:
    return (
        f'{payload["teacher_name"]} criou a atividade "{payload["activity_title"]}" para você, '
        f"com prazo em {_format_date(payload['deadline'])}."
    )


def _change_text(change: Payload) -> str:
    field, old, new = change["field"], change["old"], change["new"]
    if field == "title":
        return f'título de "{old}" para "{new}"'
    if field == "deadline":
        return f"prazo de {_format_date(old)} para {_format_date(new)}"
    # The description can be long: the payload keeps both texts, the message only says it changed.
    return "a descrição foi alterada"


def _activity_changed(payload: Payload) -> str:
    changes = "; ".join(_change_text(change) for change in payload["changes"])
    return (
        f'{payload["teacher_name"]} alterou a atividade "{payload["activity_title"]}": {changes}.'
    )


def _activity_deleted(payload: Payload) -> str:
    return f'{payload["teacher_name"]} excluiu a atividade "{payload["activity_title"]}".'


def _activity_submitted(payload: Payload) -> str:
    return (
        f'{payload["student_name"]} entregou arquivos da atividade "{payload["activity_title"]}".'
    )


def _activity_awaiting_review(payload: Payload) -> str:
    return (
        f'{payload["student_name"]} enviou a atividade "{payload["activity_title"]}" para revisão.'
    )


def _activity_commented(payload: Payload) -> str:
    return f'{payload["author_name"]} comentou na atividade "{payload["activity_title"]}".'


def _count_phrase(count: int, none: str, one: str, many: str) -> str:
    if count == 0:
        return none
    return one if count == 1 else many.format(count=count)


def _advising_ended(payload: Payload) -> str:
    activities = _count_phrase(
        payload["archived_activities"],
        "Nenhuma atividade em aberto foi arquivada.",
        "1 atividade em aberto foi arquivada.",
        "{count} atividades em aberto foram arquivadas.",
    )
    meetings = _count_phrase(
        payload["cancelled_meetings"],
        "Nenhuma reunião pendente ou aceita foi cancelada.",
        "1 reunião pendente ou aceita foi cancelada.",
        "{count} reuniões pendentes ou aceitas foram canceladas.",
    )
    return f"{payload['teacher_name']} encerrou a sua orientação. {activities} {meetings}"


def _format_datetime(iso: str) -> str:
    return datetime.datetime.fromisoformat(iso).strftime("%d/%m/%Y às %H:%M")


def _meeting_requested(payload: Payload) -> str:
    when = _format_datetime(payload["scheduled_at"])
    if payload["author_type"] == "teacher":
        return f"{payload['author_name']} marcou uma reunião com você para {when}."
    return f"{payload['author_name']} solicitou uma reunião para {when}."


def _meeting_answered(payload: Payload) -> str:
    accepted = payload["decision"] == "accepted"
    text = (
        f"{payload['responder_name']} {'aceitou' if accepted else 'recusou'} a reunião de "
        f"{_format_datetime(payload['scheduled_at'])}."
    )
    if not accepted and payload.get("reason"):
        text += f" Justificativa: {payload['reason']}"
    return text


def _advising_request_received(payload: Payload) -> str:
    return f"{payload['student_name']} enviou uma solicitação de orientação."


def _advising_request_answered(payload: Payload) -> str:
    verb = "aceitou" if payload["decision"] == "accepted" else "recusou"
    return f"{payload['teacher_name']} {verb} a sua solicitação de orientação."


def _advising_request_unanswered(payload: Payload) -> str:
    return (
        f"{payload['teacher_name']} não respondeu a sua solicitação de orientação em 7 dias. "
        "Você já pode enviar uma nova."
    )


def _activity_review_result(payload: Payload) -> str:
    accepted = payload["decision"] == "accepted"
    text = (
        f"{payload['teacher_name']} {'aceitou' if accepted else 'recusou'} a atividade "
        f'"{payload["activity_title"]}".'
    )
    if not accepted and payload.get("justification"):
        text += f" Justificativa: {payload['justification']}"
    return text


_BUILDERS: dict[str, Callable[[Payload], str]] = {
    NotificationType.ADVISING_REQUEST_RECEIVED: _advising_request_received,
    NotificationType.ADVISING_REQUEST_ANSWERED: _advising_request_answered,
    NotificationType.ADVISING_REQUEST_UNANSWERED: _advising_request_unanswered,
    NotificationType.ACTIVITY_CREATED: _activity_created,
    NotificationType.ACTIVITY_CHANGED: _activity_changed,
    NotificationType.ACTIVITY_DELETED: _activity_deleted,
    NotificationType.ACTIVITY_REVIEW_RESULT: _activity_review_result,
    NotificationType.ACTIVITY_SUBMITTED: _activity_submitted,
    NotificationType.ACTIVITY_AWAITING_REVIEW: _activity_awaiting_review,
    NotificationType.ACTIVITY_COMMENTED: _activity_commented,
    NotificationType.ADVISING_ENDED: _advising_ended,
    NotificationType.MEETING_REQUESTED: _meeting_requested,
    NotificationType.MEETING_ANSWERED: _meeting_answered,
}


def message_for(notification_type: str, payload: Payload) -> str:
    """The text to show. A type without a message of its own, or a payload that lacks or mangles the fields its
    message needs (an old row, a manual edit), falls back to the label of the type.
    """
    label = NotificationType(notification_type).label
    builder = _BUILDERS.get(notification_type)
    if builder is None:
        return label
    try:
        return builder(payload)
    except (KeyError, ValueError, TypeError):
        # An old or hand-edited payload must never break the whole list.
        return label
