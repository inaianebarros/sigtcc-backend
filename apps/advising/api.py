from django.http import HttpRequest
from ninja.responses import Status
from ninja_extra import api_controller, http_post
from ninja_jwt.authentication import JWTAuth

from apps.accounts.permissions import IsStudent

from .schemas import AdvisingRequestIn, AdvisingRequestOut, ErrorOut
from .services import request_advising


@api_controller("/orientacao", tags=["Orientação"], auth=JWTAuth())
class AdvisingRequestController:
    @http_post(
        "/solicitacoes",
        response={201: AdvisingRequestOut, 404: ErrorOut, 409: ErrorOut},
        permissions=[IsStudent],
        summary="O Aluno solicita orientação a um Professor",
    )
    def create_request(self, request: HttpRequest, payload: AdvisingRequestIn):
        advising_request = request_advising(request.user.student, payload.teacher_id)
        return Status(201, AdvisingRequestOut.from_request(advising_request))
