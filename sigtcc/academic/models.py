from django.db.models import CharField
from django.db.models import CASCADE
from django.db.models import ForeignKey

from core.base_models import BaseModelWithHistory


class ExpertiseArea(BaseModelWithHistory):
    description = CharField(max_length=255)
    name = CharField(max_length=255, unique=True)


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


class Institute(BaseModelWithHistory):
    description = CharField('Descrição', max_length=255)
    name = CharField('Nome', max_length=255, unique=True)
