from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db.models import CharField
from django.db.models import DateField
from django.db.models import DateTimeField
from django.db.models import DO_NOTHING
from django.db.models import FileField
from django.db.models import ForeignKey
from django.db.models import TextChoices
from django.db.models import TextField
from django.utils.deconstruct import deconstructible

from core.base_models import BaseModel
from core.base_models import BaseModelWithHistory
from user.models import ProfessorProfile
from user.models import StudentProfile


@deconstructible
class DocumentFilePath:
    def __call__(self, document_instance: 'Document', filename: str) -> str:
        return (
            f'student/{document_instance.task_tcc.student.uuid}/'
            f'{document_instance.task_tcc.uuid}/{filename}'
        )


class Document(BaseModel):
    file = FileField(
        'Arquivo',
        upload_to=DocumentFilePath(),
        storage=FileSystemStorage(location=settings.MEDIA_ROOT),
    )
    name = CharField('Nome', max_length=255)
    task_tcc = ForeignKey(
        'TaskTCC',
        on_delete=DO_NOTHING,
        related_name='documents',
        related_query_name='document',
        verbose_name='Tarefa',
    )

    class Meta:
        verbose_name = 'Documento'
        verbose_name_plural = 'Documentos'


class SupervisionRequest(BaseModelWithHistory):
    class ANSWER(TextChoices):
        YES = 'YES', 'Sim'
        NO = 'NO', 'Não'
        NO_ANSWER = 'NO_ANSWER', 'Sem Resposta'

    answer = CharField(
        'Resposta',
        choices=ANSWER.choices,
        default=ANSWER.NO_ANSWER,
        max_length=10,
    )
    professor = ForeignKey(
        ProfessorProfile,
        on_delete=DO_NOTHING,
        related_name='supervision_requests',
        related_query_name='supervision_request',
        verbose_name='Professor',
    )
    professor_message = TextField('Mensagem do Professor', max_length=1500)
    student = ForeignKey(
        StudentProfile,
        on_delete=DO_NOTHING,
        related_name='supervision_requests',
        related_query_name='supervision_request',
        verbose_name='Aluno',
    )
    student_message = TextField('Mensagem do Aluno', max_length=1500)
    tcc = ForeignKey(
        'TCC',
        null=True,
        on_delete=DO_NOTHING,
        related_name='supervision_requests',
        related_query_name='supervision_request',
        verbose_name='TCC',
    )

    class Meta:
        verbose_name = 'Solicitação de Supervisão'
        verbose_name_plural = 'Solicitações de Supervisão'

    def __str__(self) -> str:
        return f'{self.student} - {self.professor}'


class TaskTCC(BaseModelWithHistory):
    class TASK_STATUS(TextChoices):
        COMPLETE = 'COMPLETE', 'Concluída'
        IMCOMPLETE = 'INCOMPLETE', 'Incompleta'
        NOT_DONE = 'NOT_DONE', 'Não Iniciada'

    description = TextField('Descrição', max_length=1500)
    document_task = ForeignKey(
        Document,
        on_delete=DO_NOTHING,
        related_name='tcc_tasks',
        related_query_name='tcc_task',
        verbose_name='Documento',
    )
    due_date = DateTimeField('Data de Entrega')
    professor = ForeignKey(
        ProfessorProfile,
        on_delete=DO_NOTHING,
        related_name='tasks_tcc',
        related_query_name='tcc_task',
        verbose_name='Professor',
    )
    task_status = CharField(
        'Status',
        choices=TASK_STATUS.choices,
        max_length=12,
    )
    title = CharField('Título', max_length=255)
    tcc = ForeignKey(
        'TCC',
        on_delete=DO_NOTHING,
        related_name='tcc_tasks',
        related_query_name='tcc_task',
        verbose_name='TCC',
    )
    student = ForeignKey(
        StudentProfile,
        on_delete=DO_NOTHING,
        related_name='tcc_tasks',
        related_query_name='tcc_task',
        verbose_name='Aluno',
    )


class TCC(BaseModelWithHistory):
    presentation_date = DateTimeField('Data de Apresentação', null=True)
    professor = ForeignKey(
        ProfessorProfile,
        on_delete=DO_NOTHING,
        related_name='tccs',
        related_query_name='tcc',
        verbose_name='Professor',
    )
    start_date = DateField('Data de Início', null=True)
    student = ForeignKey(
        StudentProfile,
        on_delete=DO_NOTHING,
        related_name='tccs',
        related_query_name='tcc',
        verbose_name='Aluno',
    )
    theme = CharField('Tema', max_length=255)

    class Meta:
        verbose_name = 'TCC'
        verbose_name_plural = 'TCCs'

    def __str__(self) -> str:
        return f'{self.student} - {self.theme}'
