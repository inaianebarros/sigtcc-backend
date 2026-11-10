"""Small steps shared by the journey tests: each one is a real call to an endpoint."""

import datetime
from zoneinfo import ZoneInfo

from django.utils import timezone

from tests.api.helpers import bearer, post_json

SANTAREM = ZoneInfo("America/Santarem")


def at(days=0):
    """A date and a time (HH:MM) in Santarém, a given number of days from now."""
    moment = timezone.now().astimezone(SANTAREM) + datetime.timedelta(days=days)
    return moment.date().isoformat(), moment.strftime("%H:%M")


def search_advisors(client, student):
    return client.get("/api/orientadores/busca", headers=bearer(student.user))


def request_advising(client, student, teacher):
    return post_json(
        client,
        "/api/orientacao/solicitacoes",
        {"teacher_id": teacher.user_id},
        headers=bearer(student.user),
    )


def answer_request(client, teacher, request_id, action):
    return post_json(
        client,
        f"/api/orientacao/solicitacoes/{request_id}/responder",
        {"action": action},
        headers=bearer(teacher.user),
    )


def my_request_status(client, student):
    return client.get("/api/orientacao/minha-solicitacao", headers=bearer(student.user)).json()


def request_meeting(client, user, days, description="Revisar o capítulo 2 do TCC", student=None):
    date, time = at(days)
    body = {"date": date, "time": time, "description": description}
    if student is not None:
        body["student_id"] = student.user_id
    return post_json(client, "/api/reunioes", body, headers=bearer(user))


def answer_meeting(client, user, meeting_id, action, reason=None):
    body = {"action": action}
    if reason is not None:
        body["reason"] = reason
    return post_json(client, f"/api/reunioes/{meeting_id}/responder", body, headers=bearer(user))


def end_advising(client, teacher, advising_id, reason="Concluímos o ciclo de orientação do TCC."):
    return post_json(
        client,
        f"/api/orientacao/{advising_id}/encerrar",
        {"reason": reason},
        headers=bearer(teacher.user),
    )


def put_topic(client, student, description="Redes neurais aplicadas à saúde"):
    return client.put(
        "/api/tcc/tema",
        data={"description": description},
        content_type="application/json",
        headers=bearer(student.user),
    )
