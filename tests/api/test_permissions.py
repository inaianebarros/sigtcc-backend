import pytest
from django.urls import path
from ninja_extra import NinjaExtraAPI, api_controller, http_get
from ninja_jwt.authentication import JWTAuth
from ninja_jwt.tokens import RefreshToken

from apps.accounts.models import UserType
from apps.accounts.permissions import IsAdministrator, IsStudent, IsTeacher
from tests.factories import make_user

probe_api = NinjaExtraAPI(urls_namespace="permission-probe")


@api_controller("/probe", auth=JWTAuth())
class ProbeController:
    @http_get("/student", permissions=[IsStudent])
    def student_only(self):
        return {"ok": True}

    @http_get("/teacher", permissions=[IsTeacher])
    def teacher_only(self):
        return {"ok": True}

    @http_get("/admin", permissions=[IsAdministrator])
    def administrator_only(self):
        return {"ok": True}


probe_api.register_controllers(ProbeController)
urlpatterns = [path("probe-api/", probe_api.urls)]

pytestmark = [pytest.mark.django_db, pytest.mark.urls(__name__)]

ROUTE_BY_TYPE = {
    UserType.STUDENT: "/probe-api/probe/student",
    UserType.TEACHER: "/probe-api/probe/teacher",
    UserType.ADMIN: "/probe-api/probe/admin",
}


def bearer(user_type):
    user = make_user(user_type)
    return {"Authorization": f"Bearer {RefreshToken.for_user(user).access_token}"}


@pytest.mark.parametrize("user_type", list(UserType))
def test_each_user_type_reaches_only_its_own_route(client, user_type):
    headers = bearer(user_type)

    for route_type, route in ROUTE_BY_TYPE.items():
        expected = 200 if route_type == user_type else 403
        assert client.get(route, headers=headers).status_code == expected


@pytest.mark.parametrize("route", ROUTE_BY_TYPE.values())
def test_routes_require_authentication(client, route):
    assert client.get(route).status_code == 401
