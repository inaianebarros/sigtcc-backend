from django.http.request import HttpRequest
from ninja_extra import api_controller
from ninja_extra import ControllerBase
from ninja_extra import route
from ninja_extra import status
from ninja_extra.permissions import IsAuthenticated
from ninja_jwt.authentication import JWTAuth

from sigtcc.schemas import ReturnSchema
from tcc.models import SupervisionRequest
from tcc.schemas import StudentSupervisionRequestSchemaIn
from tcc.schemas import StudentSupervisionRequestSchemaOut
from user.models import ProfessorProfile


@api_controller(
    '/supervision-requests',
    auth=JWTAuth(),
    permissions=[IsAuthenticated],
    tags=['Supervision Requests'],
)
class SupervisionRequestController(ControllerBase):
    @route.post(
        '/student',
        response={
            status.HTTP_201_CREATED: StudentSupervisionRequestSchemaOut,
            status.HTTP_400_BAD_REQUEST: ReturnSchema,
            status.HTTP_500_INTERNAL_SERVER_ERROR: ReturnSchema,
        },
    )
    def request_supervision(
        self, request: HttpRequest, student_request: StudentSupervisionRequestSchemaIn
    ):
        if not (
            professor := ProfessorProfile.objects.filter(
                uuid=student_request.professor_uuid
            ).first()
        ):
            return status.HTTP_400_BAD_REQUEST, ReturnSchema(detail='Professor not found')

        student = request.user.student_profiles.first()

        if SupervisionRequest.objects.filter(professor=professor, student=student).exists():
            return status.HTTP_400_BAD_REQUEST, ReturnSchema(detail='Request already exists')

        try:
            supervision_request = SupervisionRequest.objects.create(
                student=student,
                professor=professor,
                student_message=student_request.student_message,
            )
        except Exception as exc:
            return status.HTTP_500_INTERNAL_SERVER_ERROR, ReturnSchema(detail=str(exc))

        return status.HTTP_201_CREATED, supervision_request
