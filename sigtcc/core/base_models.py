import uuid

from django.db.models import CharField
from django.db.models import DateTimeField
from django.db.models import Model
from django.db.models import TextChoices
from django.db.models import UUIDField
from simple_history.models import HistoricalRecords


class BaseModel(Model):
    class STATUS(TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        INACTIVE = 'INACTIVE', 'Inactive'

    created_at = DateTimeField('Data de Criação', auto_now_add=True)
    status = CharField('Status', max_length=10, choices=STATUS.choices, default=STATUS.ACTIVE)
    updated_at = DateTimeField('Data de Atualização', auto_now=True)
    uuid = UUIDField('UUID', unique=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class BaseModelWithHistory(BaseModel):
    history = HistoricalRecords('Histórico', inherit=True)

    class Meta:
        abstract = True
