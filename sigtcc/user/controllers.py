from django.http.request import HttpRequest
from django.db import transaction
from ninja.constants import NOT_SET
from ninja_extra import api_controller
from ninja_extra import route
from ninja_extra import status
from ninja_jwt.controller import TokenBlackListController
from ninja_jwt.controller import TokenObtainPairController

from sigtcc.schemas import ReturnSchema
from user.models import User
from user.schemas import UserSchemaIn


@api_controller('/users', auth=NOT_SET, permissions=[], tags=['Users'])
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
