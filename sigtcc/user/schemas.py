from ninja import ModelSchema

from academic.models import ExpertiseArea
from academic.models import Institute
from academic.schemas import CourseSchemaOut
from user.models import ProfessorProfile
from user.models import StudentProfile
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


class UserSchemaLoginOut(ModelSchema):
    access: str
    refresh: str

    class Meta:
        model = User
        fields = ['role', 'username']


class ProfessorSchemaOut(ModelSchema):
    expertise_areas: list[ExpertiseAreaSchemaOut]
    institute: InstituteSchemaOut
    user: UserSchemaOut

    class Meta:
        model = ProfessorProfile
        fields = ['biography', 'lattes_url', 'uuid']


class StudentSchemaOut(ModelSchema):
    course: CourseSchemaOut
    user: UserSchemaOut

    class Meta:
        model = StudentProfile
        fields = ['course', 'enrollment', 'uuid', 'user']
