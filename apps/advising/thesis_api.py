from datetime import datetime
from typing import Annotated

from django.http import HttpRequest
from ninja import Schema
from ninja.responses import Status
from ninja_extra import api_controller, http_get, http_put
from ninja_jwt.authentication import JWTAuth
from pydantic import StringConstraints

from apps.accounts.permissions import IsStudent, IsTeacher
from config.schemas import ErrorOut, FieldErrorsOut

from .models import ThesisTopic
from .thesis import (
    active_advising_between,
    active_advising_of_student,
    topic_of,
    write_topic,
)

TOPIC_MIN_LENGTH = 10
TOPIC_MAX_LENGTH = 1000

TopicDescription = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=TOPIC_MIN_LENGTH, max_length=TOPIC_MAX_LENGTH
    ),
]


class ThesisTopicIn(Schema):
    description: TopicDescription


class ThesisTopicOut(Schema):
    description: str | None
    updated_at: datetime | None

    @classmethod
    def from_topic(cls, topic: ThesisTopic | None) -> "ThesisTopicOut":
        if topic is None:
            return cls(description=None, updated_at=None)
        return cls(description=topic.description, updated_at=topic.updated_at)


def _written(topic: ThesisTopic, created: bool) -> Status:
    return Status(201 if created else 200, ThesisTopicOut.from_topic(topic))


@api_controller("/tcc/tema", tags=["Tema do TCC"], auth=JWTAuth())
class StudentThesisTopicController:
    @http_get(
        "",
        response={200: ThesisTopicOut, 401: ErrorOut, 403: ErrorOut},
        permissions=[IsStudent],
        summary="Tema do TCC da orientação ativa do Aluno",
        description=("Sem tema, ou sem orientação ativa, devolve description e updated_at nulos."),
    )
    def read(self, request: HttpRequest):
        advising = active_advising_of_student(request.user.student)
        return ThesisTopicOut.from_topic(topic_of(advising))

    @http_put(
        "",
        response={
            200: ThesisTopicOut,
            201: ThesisTopicOut,
            401: ErrorOut,
            403: ErrorOut,
            409: ErrorOut,
            422: FieldErrorsOut,
        },
        permissions=[IsStudent],
        summary="O Aluno registra ou altera o tema do TCC",
        description=(
            "Grava ou substitui o tema da orientação ativa: 201 ao criar e 200 ao alterar. "
            "Sem orientação ativa, 409 (RN12)."
        ),
    )
    def write(self, request: HttpRequest, payload: ThesisTopicIn):
        advising = active_advising_of_student(request.user.student)
        return _written(*write_topic(advising, payload.description))


@api_controller("/tcc/orientandos", tags=["Tema do TCC"], auth=JWTAuth())
class AdvisorThesisTopicController:
    @http_get(
        "/{int:student_id}/tema",
        response={200: ThesisTopicOut, 401: ErrorOut, 403: ErrorOut, 404: ErrorOut},
        permissions=[IsTeacher],
        summary="Tema do TCC de um orientando do Professor",
        description=(
            "Só do orientador ativo do Aluno. Sem orientação ativa com o Professor (sem vínculo, "
            "só encerrada ou de outro Professor) e id desconhecido respondem o mesmo 404 (RN32). "
            "Sem tema, devolve description e updated_at nulos."
        ),
    )
    def read(self, request: HttpRequest, student_id: int):
        advising = active_advising_between(request.user.teacher, student_id)
        return ThesisTopicOut.from_topic(topic_of(advising))

    @http_put(
        "/{int:student_id}/tema",
        response={
            200: ThesisTopicOut,
            201: ThesisTopicOut,
            401: ErrorOut,
            403: ErrorOut,
            404: ErrorOut,
            422: FieldErrorsOut,
        },
        permissions=[IsTeacher],
        summary="O orientador registra ou altera o tema do TCC do orientando",
        description=(
            "Grava ou substitui o tema da orientação ativa: 201 ao criar e 200 ao alterar. "
            "Sem orientação ativa com o Professor, o mesmo 404 da leitura."
        ),
    )
    def write(self, request: HttpRequest, student_id: int, payload: ThesisTopicIn):
        advising = active_advising_between(request.user.teacher, student_id)
        return _written(*write_topic(advising, payload.description))
