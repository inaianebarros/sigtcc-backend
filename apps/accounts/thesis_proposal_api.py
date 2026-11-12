import datetime
from typing import Annotated

from django.http import HttpRequest
from ninja import Field, Schema
from ninja.responses import Status
from ninja_extra import api_controller, http_delete, http_get, http_patch, http_post, paginate
from ninja_jwt.authentication import JWTAuth
from pydantic import StringConstraints

from config.pagination import MAX_OFFSET, BoundedLimitOffsetPagination
from config.schemas import ErrorOut, FieldErrorsOut, Page

from .permissions import IsStudent, IsTeacher
from .thesis_proposals import (
    create_proposal,
    delete_proposal,
    edit_proposal,
    list_proposals,
    list_proposals_for_student,
)

TITLE_MIN_LENGTH = 5
TITLE_MAX_LENGTH = 150
PROPOSALS_PAGE_SIZE = 5

ProposalTitle = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=TITLE_MIN_LENGTH, max_length=TITLE_MAX_LENGTH
    ),
]


class ProposalsPagination(BoundedLimitOffsetPagination):
    """Pages of 5 (ADR 0013): the interface chooses between list and table from the total."""

    class Input(Schema):
        limit: int = Field(PROPOSALS_PAGE_SIZE, ge=1, le=PROPOSALS_PAGE_SIZE)
        offset: int = Field(0, ge=0, le=MAX_OFFSET)


class ThesisProposalIn(Schema):
    title: ProposalTitle


class ThesisProposalOut(Schema):
    id: int
    title: str
    created_at: datetime.datetime


PAGE_DESCRIPTION = (
    "Da proposta mais recente para a mais antiga, depois por id, estável entre páginas. Páginas "
    "de 5 (limit padrão 5, máximo 5) com offset, e o total em count (RNF26). Sem propostas, a "
    "lista vem vazia com count 0."
)


@api_controller("/professores", tags=["Propostas de Tema"], auth=JWTAuth())
class TeacherThesisProposalController:
    @http_get(
        "/me/propostas",
        response={200: Page[ThesisProposalOut], 401: ErrorOut, 403: ErrorOut, 422: FieldErrorsOut},
        permissions=[IsTeacher],
        summary="Lista as Propostas de Tema do próprio Professor",
        description=PAGE_DESCRIPTION,
    )
    @paginate(ProposalsPagination)
    def list_own(self, request: HttpRequest):
        return list_proposals(request.user.teacher)

    @http_post(
        "/me/propostas",
        response={
            201: ThesisProposalOut,
            401: ErrorOut,
            403: ErrorOut,
            409: ErrorOut,
            422: FieldErrorsOut,
        },
        permissions=[IsTeacher],
        summary="O Professor cadastra uma Proposta de Tema",
        description=(
            "Título de 5 a 150 caracteres, sem contar os espaços das pontas (RN41). No máximo 50 "
            "propostas por Professor: a 51ª responde 409. Não se repete o título do mesmo "
            "Professor, sem diferenciar maiúsculas, acentos e espaços das pontas: responde 422. "
            "Professores diferentes podem ter o mesmo título. Salva na hora, fora do PATCH do "
            "perfil (ADR 0013)."
        ),
    )
    def create(self, request: HttpRequest, payload: ThesisProposalIn):
        return Status(201, create_proposal(request.user.teacher, payload.title))

    @http_patch(
        "/me/propostas/{int:proposal_id}",
        response={
            200: ThesisProposalOut,
            401: ErrorOut,
            403: ErrorOut,
            404: ErrorOut,
            422: FieldErrorsOut,
        },
        permissions=[IsTeacher],
        summary="O Professor edita o título de uma Proposta de Tema",
        description=(
            "Mesmas regras de título do cadastro. Proposta de outro Professor ou inexistente "
            "respondem o mesmo 404."
        ),
    )
    def edit(self, request: HttpRequest, proposal_id: int, payload: ThesisProposalIn):
        return edit_proposal(request.user.teacher, proposal_id, payload.title)

    @http_delete(
        "/me/propostas/{int:proposal_id}",
        response={204: None, 401: ErrorOut, 403: ErrorOut, 404: ErrorOut},
        permissions=[IsTeacher],
        summary="O Professor remove uma Proposta de Tema",
        description=(
            "A remoção é definitiva (RF55). Proposta de outro Professor ou inexistente "
            "respondem o mesmo 404."
        ),
    )
    def remove(self, request: HttpRequest, proposal_id: int):
        delete_proposal(request.user.teacher, proposal_id)
        return Status(204, None)


@api_controller("/professores", tags=["Propostas de Tema"], auth=JWTAuth())
class StudentThesisProposalController:
    @http_get(
        "/{int:teacher_id}/propostas",
        response={200: Page[ThesisProposalOut], 401: ErrorOut, 403: ErrorOut, 404: ErrorOut},
        permissions=[IsStudent],
        summary="Lista as Propostas de Tema de um Professor do curso do Aluno",
        description=(
            "Só Professores ativos do curso do Aluno (RN28, RN42); outro curso, Professor inativo "
            "e id desconhecido respondem o mesmo 404, e as propostas aparecem mesmo com o "
            "Professor indisponível (RN26). Somente leitura. " + PAGE_DESCRIPTION
        ),
    )
    @paginate(ProposalsPagination)
    def list_for_student(self, request: HttpRequest, teacher_id: int):
        return list_proposals_for_student(request.user.student, teacher_id)
