from ninja import ModelSchema

from academic.models import ExpertiseArea
from academic.models import Institute
from user.models import ProfessorProfile
from user.models import User


class ExpertiseAreaSchemaOut(ModelSchema):
    class Meta:
        model = ExpertiseArea
        fields = ['name', 'uuid']


class InstituteSchemaOut(ModelSchema):
    class Meta:
        model = Institute
        fields = ['name', 'uuid']


class UserSchemaIn(ModelSchema):
    class Meta:
        model = User
        fields = ['email', 'first_name', 'password']


class UserSchemaOut(ModelSchema):
    class Meta:
        model = User
        fields = ['email', 'first_name', 'uuid']


class ProfessorSchemaOut(ModelSchema):
    expertise_areas: list[ExpertiseAreaSchemaOut]
    institute: InstituteSchemaOut
    user: UserSchemaOut

    class Meta:
        model = ProfessorProfile
        fields = ['uuid']
