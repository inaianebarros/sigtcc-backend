from django.http import HttpRequest
from ninja_extra import ControllerBase
from ninja_extra.permissions import BasePermission

from .models import UserType


class _HasUserType(BasePermission):
    user_type: UserType
    message = "Você não tem permissão para realizar esta ação."

    def has_permission(self, request: HttpRequest, controller: ControllerBase) -> bool:
        user = request.user
        return user.is_authenticated and user.type == self.user_type


class IsStudent(_HasUserType):
    user_type = UserType.STUDENT


class IsTeacher(_HasUserType):
    user_type = UserType.TEACHER


class IsAdministrator(_HasUserType):
    user_type = UserType.ADMIN
