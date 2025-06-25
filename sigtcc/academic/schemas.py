from ninja import ModelSchema

from academic.models import ExpertiseArea
from academic.models import Institute


class ExpertiseAreaSchemaOut(ModelSchema):
    class Meta:
        model = ExpertiseArea
        fields = ['name', 'uuid']


class InstituteSchemaOut(ModelSchema):
    class Meta:
        model = Institute
        fields = ['name', 'uuid']
