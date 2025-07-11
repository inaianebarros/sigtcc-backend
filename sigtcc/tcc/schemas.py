from ninja import ModelSchema

from tcc.models import SupervisionRequest
from user.schemas import ProfessorSchemaOut
from user.schemas import StudentSchemaOut


class ProfessorAnswerSupervisionRequestSchemaIn(ModelSchema):
    class Meta:
        model = SupervisionRequest
        fields = ['answer', 'professor_message', 'uuid']


class StudentSupervisionRequestSchemaIn(ModelSchema):
    professor_uuid: str

    class Meta:
        model = SupervisionRequest
        fields = ['student_message']


class StudentSupervisionRequestSchemaOut(ModelSchema):
    professor: ProfessorSchemaOut
    student: StudentSchemaOut

    class Meta:
        model = SupervisionRequest
        fields = ['answer', 'student_message', 'professor_message', 'tcc', 'uuid']
