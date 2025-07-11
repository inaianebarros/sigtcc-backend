from ninja import ModelSchema

from academic.models import Course
from academic.models import ExpertiseArea
from academic.models import Institute


class CourseSchemaOut(ModelSchema):
    class Meta:
        model = Course
        fields = ['name']


class ExpertiseAreaSchemaOut(ModelSchema):
    class Meta:
        model = ExpertiseArea
        fields = ['name', 'uuid']


class InstituteSchemaOut(ModelSchema):
    class Meta:
        model = Institute
        fields = ['name', 'uuid']
