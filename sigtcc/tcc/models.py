from django.db.models import CharField
from django.db.models import DateField
from django.db.models import DateTimeField
from django.db.models import DO_NOTHING
from django.db.models import ForeignKey

from core.base_models import BaseModelWithHistory
from user.models import ProfessorProfile
from user.models import StudentProfile


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
