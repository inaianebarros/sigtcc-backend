from django.http import HttpRequest
from ninja import Query, Schema
from ninja_extra import api_controller, http_get, paginate
from ninja_jwt.authentication import JWTAuth

from apps.accounts.permissions import IsStudent
from apps.accounts.schemas import AreaOfInterestOut, InstituteOut
from config.pagination import BoundedLimitOffsetPagination
from config.schemas import Page

from .advisor_search import search_advisors


class AdvisorOut(Schema):
    id: int
    full_name: str
    institute: InstituteOut
    compatibility: int
    common_areas: list[AreaOfInterestOut]
    is_available: bool

    @staticmethod
    def resolve_id(obj) -> int:
        return obj.user_id

    @staticmethod
    def resolve_full_name(obj) -> str:
        return obj.user.full_name

    @staticmethod
    def resolve_institute(obj, context) -> InstituteOut:
        institute = context["request"].user.student.course.institute
        return InstituteOut(id=institute.id, name=institute.name)


@api_controller("/orientadores", tags=["Orientadores"], auth=JWTAuth())
class AdvisorSearchController:
    @http_get(
        "/busca",
        response=Page[AdvisorOut],
        permissions=[IsStudent],
        summary="Busca de orientadores do curso do Aluno",
        description=(
            "Só Professores ativos do curso do Aluno (RN28), do que tem mais Áreas em comum para o "
            "que tem menos (RN23), depois por nome e id. O parâmetro name filtra por parte do "
            "nome, sem diferenciar maiúsculas e acentos. A disponibilidade é informada e não "
            "altera a ordem. Paginada com limit (padrão 20, máximo 100) e offset."
        ),
    )
    @paginate(BoundedLimitOffsetPagination)
    def search(self, request: HttpRequest, name: str | None = Query(None, max_length=150)):
        return search_advisors(request.user.student, name)
