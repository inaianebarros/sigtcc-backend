from ninja import ModelSchema

from tcc.models import SupervisionRequest
from user.schemas import ProfessorSchemaOut


class StudentSupervisionRequestSchemaIn(ModelSchema):
    professor_uuid: str

    class Meta:
        model = SupervisionRequest
        fields = ['student_message']


class StudentSupervisionRequestSchemaOut(ModelSchema):
    professor: ProfessorSchemaOut

    class Meta:
        model = SupervisionRequest
        fields = ['uuid', 'student_message']
