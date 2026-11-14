from datetime import date, datetime
from enum import StrEnum
from typing import Annotated

from django.http import FileResponse, HttpRequest
from django.utils import timezone
from ninja import File, Form, Schema
from ninja.files import UploadedFile
from ninja.responses import Status
from ninja_extra import api_controller, http_delete, http_get, http_patch, http_post, paginate
from ninja_jwt.authentication import JWTAuth
from pydantic import StringConstraints, field_validator, model_validator

from apps.accounts.permissions import IsStudent, IsTeacher
from apps.advising.schemas import TeacherSummaryOut
from config.pagination import BoundedLimitOffsetPagination
from config.schemas import ErrorOut, FieldErrorsOut, Page, check_partial_update

from .models import (
    Activity,
    ActivityStatus,
    Review,
    ReviewDecision,
    ReviewFile,
    Submission,
    SubmissionFile,
)
from .queries import (
    activities_summary,
    activity_detail,
    activity_item,
    list_activities,
    list_comments,
)
from .services import (
    EDITABLE_FIELDS,
    MAX_JUSTIFICATION_LENGTH,
    PAST_DEADLINE_MESSAGE,
    add_comment,
    change_activity_status,
    create_activity,
    delete_activity,
    deliver_submission,
    edit_activity,
    open_review_file,
    open_submission_file,
    review_activity,
)

TITLE_MAX_LENGTH = 200
DESCRIPTION_MAX_LENGTH = 2000
COMMENT_MAX_LENGTH = 1000

ActivityTitle = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=TITLE_MAX_LENGTH),
]
ActivityDescription = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=DESCRIPTION_MAX_LENGTH),
]


class ActivityIn(Schema):
    student_id: int
    title: ActivityTitle
    description: ActivityDescription
    deadline: date

    @field_validator("deadline")
    @classmethod
    def deadline_is_not_in_the_past(cls, value: date) -> date:
        if value < timezone.localdate():
            raise ValueError(PAST_DEADLINE_MESSAGE)
        return value


class ActivityPatchIn(Schema):
    title: ActivityTitle | None = None
    description: ActivityDescription | None = None
    deadline: date | None = None

    @model_validator(mode="after")
    def at_least_one_field_and_no_nulls(self):
        check_partial_update(self, EDITABLE_FIELDS)
        return self


StudentStatus = StrEnum(
    "StudentStatus",
    {
        "PENDING": ActivityStatus.PENDING.value,
        "IN_PROGRESS": ActivityStatus.IN_PROGRESS.value,
        "IN_REVIEW": ActivityStatus.IN_REVIEW.value,
    },
)


class ActivityStatusIn(Schema):
    status: StudentStatus


class ReviewIn(Schema):
    decision: ReviewDecision
    justification: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=MAX_JUSTIFICATION_LENGTH)
    ] = ""


class CommentIn(Schema):
    text: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=COMMENT_MAX_LENGTH)
    ]


class CommentAuthorOut(Schema):
    id: int
    full_name: str
    type: str


class CommentOut(Schema):
    id: int
    text: str
    created_at: datetime
    author: CommentAuthorOut


class ActivityOut(Schema):
    id: int
    title: str
    description: str
    deadline: date
    status: str
    created_at: datetime


ListedStatus = StrEnum(
    "ListedStatus",
    {status.name: status.value for status in ActivityStatus if status != ActivityStatus.ARCHIVED},
)


class ActivityStudentOut(Schema):
    id: int
    full_name: str


class ActivitySummaryOut(Schema):
    total: int
    pending: int
    completed: int


class ActivityItemOut(Schema):
    id: int
    title: str
    deadline: date
    status: str
    overdue: bool
    student: ActivityStudentOut

    @staticmethod
    def resolve_student(obj: Activity) -> ActivityStudentOut:
        user = obj.advising.student.user
        return ActivityStudentOut(id=user.id, full_name=user.full_name)


class SubmissionFileOut(Schema):
    id: int
    file_name: str
    size_bytes: int
    content_type: str


class SubmissionOut(Schema):
    id: int
    submitted_at: datetime
    files: list[SubmissionFileOut]

    @staticmethod
    def resolve_files(obj: Submission) -> list[SubmissionFile]:
        return list(obj.files.all())


class ReviewFileOut(Schema):
    id: int
    file_name: str
    size_bytes: int
    content_type: str


class ReviewOut(Schema):
    id: int
    decision: str
    justification: str
    decided_at: datetime
    files: list[ReviewFileOut]

    @staticmethod
    def resolve_files(obj: Review) -> list[ReviewFile]:
        return list(obj.files.all())


class ActivityDetailOut(ActivityItemOut):
    teacher: TeacherSummaryOut
    description: str
    created_at: datetime
    submissions: list[SubmissionOut]
    reviews: list[ReviewOut]
    comments_count: int

    @staticmethod
    def resolve_teacher(obj: Activity) -> TeacherSummaryOut:
        return TeacherSummaryOut.from_teacher(obj.advising.teacher)

    @staticmethod
    def resolve_submissions(obj: Activity) -> list[Submission]:
        return obj.ordered_submissions

    @staticmethod
    def resolve_reviews(obj: Activity) -> list[Review]:
        return obj.ordered_reviews


def attachment_response(stored_file: SubmissionFile | ReviewFile, content) -> FileResponse:
    """A stored file as a download: an attachment under its original name, with the stored type and
    nosniff set by the route itself, whatever the global setting says."""
    response = FileResponse(
        content,
        as_attachment=True,
        filename=stored_file.file_name,
        content_type=stored_file.content_type,
    )
    response["X-Content-Type-Options"] = "nosniff"
    return response


@api_controller("/atividades", tags=["Atividades"], auth=JWTAuth())
class ActivityController:
    @http_post(
        "",
        response={
            201: ActivityOut,
            401: ErrorOut,
            403: ErrorOut,
            404: ErrorOut,
            422: FieldErrorsOut,
        },
        permissions=[IsTeacher],
        summary="O Professor cria uma atividade para um orientando",
        description=(
            "A atividade nasce pendente e pertence à orientação ativa do Aluno. O prazo é uma "
            "data de hoje em diante (data do servidor). Sem orientação ativa com o Aluno (sem "
            "vínculo, só encerrada ou de outro Professor) e id desconhecido respondem o mesmo "
            "404, sem revelar o Aluno (RN13)."
        ),
    )
    def create(self, request: HttpRequest, payload: ActivityIn):
        activity: Activity = create_activity(
            request.user.teacher,
            payload.student_id,
            payload.title,
            payload.description,
            payload.deadline,
        )
        return Status(201, activity)

    @http_get(
        "",
        response={200: Page[ActivityItemOut], 401: ErrorOut, 403: ErrorOut, 422: FieldErrorsOut},
        permissions=[IsStudent | IsTeacher],
        summary="Lista as atividades do Aluno ou dos orientandos do Professor",
        description=(
            "O Aluno vê as da própria orientação ativa e o Professor as de todos os orientandos "
            "ativos. Excluídas, arquivadas e de orientações encerradas não aparecem. Filtros "
            "combinados por E: status (pending, in_progress, in_review ou completed), overdue "
            "(true só as atrasadas, false só as não atrasadas), student_id, dentro do escopo de "
            "quem consulta, e search, que filtra só pelo título, por texto contido, sem "
            "diferenciar maiúsculas e acentos, com os espaços das pontas removidos e no máximo "
            "200 caracteres. As abertas vêm primeiro, pelo prazo mais próximo, e depois as "
            "concluídas, do prazo mais recente para o mais antigo (RN24). Paginada com limit "
            "(padrão 20, máximo 100) e offset."
        ),
    )
    @paginate(BoundedLimitOffsetPagination)
    def list_my_activities(
        self,
        request: HttpRequest,
        status: ListedStatus | None = None,
        overdue: bool | None = None,
        student_id: int | None = None,
        search: str | None = None,
    ):
        return list_activities(request.user, status, overdue, student_id, search=search)

    @http_get(
        "/resumo",
        response={200: ActivitySummaryOut, 401: ErrorOut, 403: ErrorOut},
        permissions=[IsStudent | IsTeacher],
        summary="Os totais de atividades do Aluno ou dos orientandos do Professor",
        description=(
            "O total, as pendentes (só o status pending) e as concluídas, com a mesma contagem e o "
            "mesmo escopo do Dashboard (RN35): atividades não excluídas nem arquivadas, de "
            "orientações ativas, do próprio Aluno ou de todos os orientandos ativos do "
            "Professor. Em andamento e em revisão entram só no total. Não recebe filtros: a "
            "busca e os filtros da listagem nunca alteram estes números."
        ),
    )
    def summary(self, request: HttpRequest):
        return activities_summary(request.user)

    @http_get(
        "/{int:activity_id}",
        response={200: ActivityDetailOut, 401: ErrorOut, 403: ErrorOut, 404: ErrorOut},
        permissions=[IsStudent | IsTeacher],
        summary="Detalhes de uma atividade",
        description=(
            "Os campos da lista mais a descrição, a data de criação, as entregas (com nome, "
            "tamanho e tipo dos arquivos, sem endereço de download), as revisões e a contagem "
            "de comentários. Atividade fora do escopo de quem consulta, excluída, arquivada, de "
            "orientação encerrada ou inexistente responde o mesmo 404."
        ),
    )
    def read_activity(self, request: HttpRequest, activity_id: int):
        return activity_detail(request.user, activity_id)

    @http_patch(
        "/{int:activity_id}",
        response={
            200: ActivityOut,
            401: ErrorOut,
            403: ErrorOut,
            404: ErrorOut,
            409: ErrorOut,
            422: FieldErrorsOut,
        },
        permissions=[IsTeacher],
        summary="O Professor orientador edita uma atividade",
        description=(
            "Edição parcial de title, description e deadline, com ao menos um campo e os mesmos "
            "limites da criação. Só um prazo que mudou precisa ser de hoje em diante, então o "
            "título de uma atividade atrasada pode ser corrigido. Concluída responde 409 (RN15). "
            "Outro Professor, atividade excluída, inexistente ou de orientação encerrada "
            "respondem o mesmo 404 (RN14)."
        ),
    )
    def edit(self, request: HttpRequest, activity_id: int, payload: ActivityPatchIn):
        result = edit_activity(
            request.user.teacher,
            activity_id,
            title=payload.title,
            description=payload.description,
            deadline=payload.deadline,
        )
        return result.activity

    @http_delete(
        "/{int:activity_id}",
        response={204: None, 401: ErrorOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut},
        permissions=[IsTeacher],
        summary="O Professor orientador exclui uma atividade",
        description=(
            "Exclusão lógica: a atividade some da lista e do detalhe, e as entregas, revisões e "
            "comentários são preservados. Não há como desfazer. Concluída responde 409 (RN15). "
            "Outro Professor, atividade já excluída, inexistente ou de orientação encerrada "
            "respondem o mesmo 404 (RN14)."
        ),
    )
    def remove(self, request: HttpRequest, activity_id: int):
        delete_activity(request.user.teacher, activity_id)
        return Status(204, None)

    @http_post(
        "/{int:activity_id}/entregas",
        response={
            201: SubmissionOut,
            401: ErrorOut,
            403: ErrorOut,
            404: ErrorOut,
            409: ErrorOut,
            422: FieldErrorsOut,
            503: ErrorOut,
        },
        permissions=[IsStudent],
        summary="O Aluno entrega arquivos de uma atividade",
        description=(
            "Entrega em multipart, no campo files, de 1 a 5 arquivos de 1 byte a 10 MB, nos "
            "tipos PDF, DOC, DOCX, ZIP, PNG e JPG (ou JPEG) (RF31). O tipo é conferido pela extensão e "
            "pelo conteúdo, não pelo Content-Type enviado, e o nome original é guardado só como "
            "metadado. Um arquivo inválido rejeita a entrega inteira, com um erro de validação "
            "por arquivo (files.0, files.1...) e nada gravado. Entregar não muda o status da "
            "atividade, que precisa estar pendente ou em andamento (RN16): em revisão ou "
            "concluída responde 409. Outro Aluno, atividade excluída, arquivada, inexistente ou "
            "de orientação encerrada respondem o mesmo 404. A gravação é tudo ou nada: se o "
            "armazenamento falhar, responde 503 e nada fica no banco; se o banco falhar, os arquivos "
            "já enviados são removidos. Cada entrega notifica o Professor orientador na mesma "
            "transação (RF42); uma entrega recusada ou que falha não deixa aviso."
        ),
    )
    def deliver(self, request: HttpRequest, activity_id: int, files: File[list[UploadedFile]]):
        submission = deliver_submission(request.user.student, activity_id, files)
        return Status(201, submission)

    @http_get(
        "/{int:activity_id}/arquivos/{int:file_id}",
        response={401: ErrorOut, 403: ErrorOut, 404: ErrorOut, 503: ErrorOut},
        permissions=[IsStudent | IsTeacher],
        summary="Baixa um arquivo de uma entrega",
        description=(
            "Devolve o arquivo em streaming, como anexo (Content-Disposition: attachment, com o "
            "nome original codificado), com X-Content-Type-Options: nosniff e o tipo guardado. "
            "Baixam o Aluno da atividade e o seu Professor orientador ativo, em qualquer status "
            "da atividade. Qualquer outra pessoa, atividade excluída, arquivada ou de orientação "
            "encerrada, arquivo inexistente (ou ausente do armazenamento) ou de outra atividade "
            "respondem o mesmo 404. Se o armazenamento não responder, 503."
        ),
        openapi_extra={
            "responses": {
                "200": {
                    "description": "O conteúdo do arquivo.",
                    "content": {
                        "application/octet-stream": {
                            "schema": {"type": "string", "format": "binary"}
                        }
                    },
                }
            }
        },
    )
    def download(self, request: HttpRequest, activity_id: int, file_id: int):
        submission_file, content = open_submission_file(request.user, activity_id, file_id)
        return attachment_response(submission_file, content)

    @http_get(
        "/{int:activity_id}/revisoes/{int:review_id}/arquivos/{int:file_id}",
        response={401: ErrorOut, 403: ErrorOut, 404: ErrorOut, 503: ErrorOut},
        permissions=[IsStudent | IsTeacher],
        summary="Baixa um anexo de uma recusa de revisão",
        description=(
            "Devolve o anexo em streaming, como anexo (Content-Disposition: attachment, com o "
            "nome original codificado), com X-Content-Type-Options: nosniff e o tipo guardado, "
            "como o download dos arquivos de entrega (RN48). Baixam o Aluno da atividade e o seu "
            "Professor orientador ativo, em qualquer status da atividade. Qualquer outra pessoa, "
            "atividade excluída, arquivada ou de orientação encerrada, anexo inexistente (ou "
            "ausente do armazenamento), de outra decisão ou de outra atividade respondem o "
            "mesmo 404. Se o armazenamento não responder, 503. Os ids vêm de reviews[].id e "
            "reviews[].files[].id do detalhe da atividade."
        ),
        openapi_extra={
            "responses": {
                "200": {
                    "description": "O conteúdo do arquivo.",
                    "content": {
                        "application/octet-stream": {
                            "schema": {"type": "string", "format": "binary"}
                        }
                    },
                }
            }
        },
    )
    def download_review_file(
        self, request: HttpRequest, activity_id: int, review_id: int, file_id: int
    ):
        review_file, content = open_review_file(request.user, activity_id, review_id, file_id)
        return attachment_response(review_file, content)

    @http_patch(
        "/{int:activity_id}/status",
        response={
            200: ActivityItemOut,
            401: ErrorOut,
            403: ErrorOut,
            404: ErrorOut,
            409: ErrorOut,
            422: FieldErrorsOut,
        },
        permissions=[IsStudent],
        summary="O Aluno altera o status de uma atividade",
        description=(
            "O Aluno alterna entre pending e in_progress, quantas vezes quiser e mesmo com a "
            "atividade atrasada, e a envia para revisão com in_review (RF28, RN15). Qualquer "
            "outro valor, inclusive completed e archived, é erro de validação. Pedir o status "
            "que a atividade já tem responde 200 sem mudar nada. in_review só sai de pending ou "
            "in_progress e exige ao menos uma entrega, mas depois de uma recusa na revisão o "
            "Aluno reenvia sem entrega nova. Em revisão o Aluno perde o controle, e concluída é "
            "definitiva: as duas respondem 409, assim como enviar para revisão sem nenhuma "
            "entrega. Outro Aluno, atividade excluída, arquivada, inexistente ou de orientação "
            "encerrada respondem o mesmo 404. Enviar para revisão notifica o Professor orientador "
            "na mesma transação, inclusive no reenvio depois de uma recusa (RF47); alternar entre "
            "pending e in_progress não notifica. A resposta é a atividade no formato da lista."
        ),
    )
    def change_status(self, request: HttpRequest, activity_id: int, payload: ActivityStatusIn):
        status = ActivityStatus(payload.status.value)
        activity = change_activity_status(request.user.student, activity_id, status)
        return activity_item(activity.id)

    @http_post(
        "/{int:activity_id}/revisar",
        response={
            200: ActivityItemOut,
            401: ErrorOut,
            403: ErrorOut,
            404: ErrorOut,
            409: ErrorOut,
            422: FieldErrorsOut,
        },
        permissions=[IsTeacher],
        summary="O Professor orientador revisa uma atividade em revisão",
        description=(
            "decision é accepted ou rejected (RF46, RN21). Aceitar conclui a atividade, de forma "
            "definitiva (RN15), e ela deixa de aparecer como atrasada. Recusar a devolve para "
            "pending, com as entregas anteriores mantidas, e exige justification de 1 a 500 "
            "caracteres, sem espaços nas pontas (RN40); no aceite, justification é erro de "
            "validação. A decisão fica no histórico de revisões da atividade, visível ao Aluno e "
            "ao Professor, e o Aluno recebe uma notificação do tipo activity_review_result, na "
            "mesma transação (RF48). Atividade que não está em revisão responde 409; duas "
            "revisões simultâneas geram só uma, e a segunda recebe 409. Outro Professor, "
            "atividade de outro Aluno, excluída, arquivada, inexistente ou de orientação "
            "encerrada (inclusive durante a ação) respondem o mesmo 404. A resposta é a "
            "atividade no formato da lista."
        ),
    )
    def review(self, request: HttpRequest, activity_id: int, payload: ReviewIn):
        activity = review_activity(
            request.user.teacher, activity_id, payload.decision, payload.justification
        )
        return activity_item(activity.id)

    @http_post(
        "/{int:activity_id}/recusar",
        response={
            200: ActivityItemOut,
            401: ErrorOut,
            403: ErrorOut,
            404: ErrorOut,
            409: ErrorOut,
            422: FieldErrorsOut,
            503: ErrorOut,
        },
        permissions=[IsTeacher],
        summary="O Professor orientador recusa uma atividade em revisão, com arquivos",
        description=(
            "Recusa em multipart (RF46, RN48): justification, de 1 a 500 caracteres sem contar as "
            "pontas (RN40), e, no campo files, de 0 a 5 arquivos de 1 byte a 10 MB, nos tipos "
            "PDF, DOC, DOCX, ZIP, PNG e JPG (ou JPEG), conferidos pela extensão e pelo conteúdo, "
            "como na entrega. Um arquivo inválido rejeita a recusa inteira, com um erro de "
            "validação por arquivo (files.0, files.1...) e nada gravado. A atividade volta para "
            "pending, com as entregas anteriores mantidas, e o Aluno recebe a notificação do "
            "resultado (RF48), igual à da rota de revisar. A decisão, os anexos e a notificação "
            "são gravados juntos: se o armazenamento falhar, responde 503, nada fica gravado e a "
            "atividade continua em revisão; se o banco falhar, os arquivos já enviados são "
            "removidos. Atividade que não está em revisão responde 409. Outro Professor, "
            "atividade de outro Aluno, excluída, arquivada, inexistente ou de orientação "
            "encerrada respondem o mesmo 404. Os anexos de cada decisão aparecem em reviews[].files "
            "do detalhe da atividade. O aceite continua em /revisar, em JSON e sem arquivos. A "
            "resposta é a atividade no formato da lista."
        ),
    )
    def refuse(
        self,
        request: HttpRequest,
        activity_id: int,
        justification: Form[str],
        files: File[list[UploadedFile]] = None,
    ):
        activity = review_activity(
            request.user.teacher,
            activity_id,
            ReviewDecision.REJECTED,
            justification.strip(),
            files or [],
        )
        return activity_item(activity.id)

    @http_post(
        "/{int:activity_id}/comentarios",
        response={
            201: CommentOut,
            401: ErrorOut,
            403: ErrorOut,
            404: ErrorOut,
            409: ErrorOut,
            422: FieldErrorsOut,
        },
        permissions=[IsStudent | IsTeacher],
        summary="O Aluno da atividade ou o seu Professor orientador comenta",
        description=(
            "text é texto simples de 1 a 1000 caracteres, sem espaços nas pontas; as quebras de "
            "linha são mantidas e HTML ou menções não têm efeito (RF32). Comentários não podem "
            "ser editados nem excluídos. Pendente, em andamento e em revisão aceitam comentário "
            "dos dois lados; concluída responde 409 (RN15). Outro Aluno ou Professor, atividade "
            "excluída, arquivada, inexistente ou de orientação encerrada (inclusive durante a "
            "ação) respondem o mesmo 404. A outra parte é notificada na mesma transação: o Aluno quando "
            "o Professor comenta (RF36) e o Professor quando o Aluno comenta (RF43)."
        ),
    )
    def comment(self, request: HttpRequest, activity_id: int, payload: CommentIn):
        created = add_comment(request.user, activity_id, payload.text)
        return Status(201, created)

    @http_get(
        "/{int:activity_id}/comentarios",
        response={200: Page[CommentOut], 401: ErrorOut, 403: ErrorOut, 404: ErrorOut},
        permissions=[IsStudent | IsTeacher],
        summary="Lista os comentários de uma atividade",
        description=(
            "Do mais antigo para o mais recente (desempate pelo id), paginada com limit (padrão "
            "20, máximo 100) e offset; count traz o total, para a tela abrir a última página. "
            "Cada item traz o autor (id, nome e type: student ou teacher). A leitura funciona em "
            "qualquer status visível, inclusive concluída. Atividade fora do escopo de quem "
            "consulta, excluída, arquivada, de orientação encerrada ou inexistente responde o "
            "mesmo 404 do detalhe."
        ),
    )
    @paginate(BoundedLimitOffsetPagination)
    def list_activity_comments(self, request: HttpRequest, activity_id: int):
        return list_comments(request.user, activity_id)
