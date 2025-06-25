from django.db import transaction
from django.http.request import HttpRequest
from ninja.constants import NOT_SET
from ninja_extra import api_controller
from ninja_extra import ControllerBase
from ninja_extra import route
from ninja_extra import status
from ninja_jwt.controller import TokenBlackListController
from ninja_jwt.controller import TokenObtainPairController

from core.constants import QUERY
from sigtcc.schemas import ReturnSchema
from user.models import ProfessorProfile
from user.models import User
from user.schemas import ProfessorSchemaOut
from user.schemas import UserSchemaIn


@api_controller('/professors', auth=NOT_SET, tags=['Professors'])
class ProfessorController(ControllerBase):
    @route.get(
        '/',
        auth=NOT_SET,
        permissions=[],
        response={
            status.HTTP_200_OK: list[ProfessorSchemaOut],
            status.HTTP_400_BAD_REQUEST: ReturnSchema,
            status.HTTP_500_INTERNAL_SERVER_ERROR: ReturnSchema,
        },
    )
    def list_professors(
        self,
        request: HttpRequest,
        expertise_areas_uuids: list[str] | None = QUERY,
        first_name: str | None = None,
        institute_uuids: str | None = None,
    ):
        qs = ProfessorProfile.objects.all()

        if expertise_areas_uuids:
            qs = qs.filter(expertise_areas__uuid__in=expertise_areas_uuids)

        if first_name:
            qs = qs.filter(user__first_name__icontains=first_name)

        if institute_uuids:
            qs = qs.filter(institute__uuid=institute_uuids)

        return qs.distinct().order_by('user__first_name')


@api_controller('/users', auth=NOT_SET, tags=['Users'])
class UserController(TokenObtainPairController, TokenBlackListController):
    @route.post(
        '/',
        auth=NOT_SET,
        permissions=[],
        response={
            status.HTTP_200_OK: ReturnSchema,
            status.HTTP_400_BAD_REQUEST: ReturnSchema,
            status.HTTP_500_INTERNAL_SERVER_ERROR: ReturnSchema,
        },
    )
    def create_user(self, request: HttpRequest, user_schema: UserSchemaIn):
        if User.objects.filter(email=user_schema.email).exists():
            return status.HTTP_400_BAD_REQUEST, ReturnSchema(detail='User already exists.')

        data = user_schema.dict(exclude=['password'])

        try:
            with transaction.atomic():
                user = User(**data, username=user_schema.email)
                user.set_password(user_schema.password)
                user.save()
        except Exception as exc:
            return status.HTTP_500_INTERNAL_SERVER_ERROR, ReturnSchema(detail=str(exc))

        return ReturnSchema(detail='Student created successfully.')
