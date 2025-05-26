from django.db.models import CharField
from django.db.models import CASCADE
from django.db.models import ForeignKey

from core.base_models import BaseModelWithHistory


class ExpertiseArea(BaseModelWithHistory):
    description = CharField(max_length=255)
    name = CharField(max_length=255, unique=True)

    class Meta:
        verbose_name = 'Área de expertise'
        verbose_name_plural = 'Áreas de expertise'

    def __str__(self):
        return self.name


class Course(BaseModelWithHistory):
    description = CharField('Descrição', max_length=255)
    institute = ForeignKey(
        'Institute',
        on_delete=CASCADE,
        related_name='courses',
        related_query_name='course',
        verbose_name='Instituição',
    )
    name = CharField('Nome', max_length=255, unique=True)

    class Meta:
        verbose_name = 'Curso'
        verbose_name_plural = 'Cursos'

    def __str__(self):
        return f'{self.institute} - {self.name}'


class Institute(BaseModelWithHistory):
    description = CharField('Descrição', max_length=255)
    name = CharField('Nome', max_length=255, unique=True)

    class Meta:
        verbose_name = 'Instituto'
        verbose_name_plural = 'Institutos'

    def __str__(self):
        return self.name
