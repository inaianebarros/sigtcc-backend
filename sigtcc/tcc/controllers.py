from django.http.request import HttpRequest
from ninja_extra import api_controller
from ninja_extra import ControllerBase
from ninja_extra import route
from ninja_extra import status
from ninja_extra.permissions import IsAuthenticated
from ninja_jwt.authentication import JWTAuth

from sigtcc.schemas import ReturnSchema
from tcc.models import SupervisionRequest
from tcc.schemas import ProfessorAnswerSupervisionRequestSchemaIn
from tcc.schemas import StudentSupervisionRequestSchemaIn
from tcc.schemas import StudentSupervisionRequestSchemaOut
from user.models import ProfessorProfile
from user.models import User


@api_controller(
    '/supervision-requests',
    auth=JWTAuth(),
    permissions=[IsAuthenticated],
    tags=['Supervision Requests'],
)
class SupervisionRequestController(ControllerBase):
    @route.post(
        '/professor',
        response={
            status.HTTP_200_OK: ReturnSchema,
            status.HTTP_400_BAD_REQUEST: ReturnSchema,
            status.HTTP_500_INTERNAL_SERVER_ERROR: ReturnSchema,
        },
    )
    def answer_supervision_request(
        self, request: HttpRequest, professor_answer: ProfessorAnswerSupervisionRequestSchemaIn
    ):
        breakpoint()
        if not (
            supervision_request := SupervisionRequest.objects.filter(
                uuid=professor_answer.uuid
            ).first()
        ):
            return status.HTTP_400_BAD_REQUEST, ReturnSchema(detail='Supervision request not found')

        if supervision_request.answer != SupervisionRequest.ANSWER.NO_ANSWER:
            return status.HTTP_400_BAD_REQUEST, ReturnSchema(
                detail='Supervision request already answered'
            )

        supervision_request.answer = professor_answer.answer
        supervision_request.professor_message = professor_answer.professor_message
        supervision_request.save()

        return status.HTTP_200_OK, ReturnSchema(detail='Supervision request answered')

    @route.get(
        '/student',
        response={
            status.HTTP_200_OK: list[StudentSupervisionRequestSchemaOut],
            status.HTTP_500_INTERNAL_SERVER_ERROR: ReturnSchema,
        },
    )
    def list_student_supervisions_request(self, request: HttpRequest) -> list[SupervisionRequest]:
        return SupervisionRequest.objects.filter(student=request.user.student_profiles.first())

    @route.get(
        '/professor',
        response={
            status.HTTP_200_OK: list[StudentSupervisionRequestSchemaOut],
            status.HTTP_500_INTERNAL_SERVER_ERROR: ReturnSchema,
        },
    )
    def list_professor_supervisions_request(self, request: HttpRequest):
        return SupervisionRequest.objects.filter(
            professor=request.user.professor_profiles.first(),
            answer=SupervisionRequest.ANSWER.NO_ANSWER,
        ).order_by('created_at')

    @route.post(
        '/student',
        response={
            status.HTTP_201_CREATED: StudentSupervisionRequestSchemaOut,
            status.HTTP_400_BAD_REQUEST: ReturnSchema,
            status.HTTP_401_UNAUTHORIZED: ReturnSchema,
            status.HTTP_500_INTERNAL_SERVER_ERROR: ReturnSchema,
        },
    )
    def request_supervision(
        self, request: HttpRequest, student_request: StudentSupervisionRequestSchemaIn
    ):
        if request.user.role == User.ROLE.PROFESSOR:
            return status.HTTP_401_UNAUTHORIZED, ReturnSchema(
                detail='Usuário professor não possui permissão para realizar requisição'
            )

        if not (
            professor := ProfessorProfile.objects.filter(
                uuid=student_request.professor_uuid
            ).first()
        ):
            return status.HTTP_400_BAD_REQUEST, ReturnSchema(detail='Professor não encontrado')

        student = request.user.student_profiles.first()

        if SupervisionRequest.objects.filter(professor=professor, student=student).exists():
            return status.HTTP_400_BAD_REQUEST, ReturnSchema(
                detail='Aluno já fez solicitação para este professor'
            )

        try:
            supervision_request = SupervisionRequest.objects.create(
                student=student,
                professor=professor,
                student_message=student_request.student_message,
            )
        except Exception as exc:
            return status.HTTP_500_INTERNAL_SERVER_ERROR, ReturnSchema(detail=str(exc))

        return status.HTTP_201_CREATED, supervision_request
