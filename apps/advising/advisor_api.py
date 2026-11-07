from django.http import HttpRequest
from ninja import Query, Schema
from ninja_extra import api_controller, http_get, paginate
from ninja_jwt.authentication import JWTAuth

from apps.accounts.permissions import IsStudent
from apps.accounts.schemas import AreaOfInterestOut, ArticleOut, InstituteOut
from config.pagination import BoundedLimitOffsetPagination
from config.schemas import ErrorOut, Page

from .advisor_profile import advisor_profile
from .advisor_search import search_advisors
from .schemas import CourseSummaryOut, ProfileAreaOut


class AdvisorBaseOut(Schema):
    id: int
    full_name: str
    institute: InstituteOut
    compatibility: int
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


class AdvisorOut(AdvisorBaseOut):
    common_areas: list[AreaOfInterestOut]


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
            "nome, sem diferenciar maiúsculas e acentos. Por padrão só aparecem os professores "
            "disponíveis; only_available=false inclui os indisponíveis (RN26). A disponibilidade "
            "é informada e não altera a ordem. Paginada com limit (padrão 20, máximo 100) e offset."
        ),
    )
    @paginate(BoundedLimitOffsetPagination)
    def search(
        self,
        request: HttpRequest,
        name: str | None = Query(None, max_length=150),
        only_available: bool = Query(
            True,
            description=(
                "Verdadeiro (padrão): só professores disponíveis para orientar. "
                "Falso: também os indisponíveis."
            ),
        ),
    ):
        return search_advisors(request.user.student, name, only_available=only_available)


class AdvisorProfileOut(AdvisorBaseOut):
    courses: list[CourseSummaryOut]
    areas: list[ProfileAreaOut]
    articles: list[ArticleOut]

    @staticmethod
    def resolve_articles(obj) -> list[ArticleOut]:
        return [ArticleOut.from_article(article) for article in obj.ordered_articles]

    @staticmethod
    def resolve_areas(obj) -> list[ProfileAreaOut]:
        return ProfileAreaOut.mark_common(obj.all_areas, {area.id for area in obj.common_areas})


@api_controller("/professores", tags=["Orientadores"], auth=JWTAuth())
class AdvisorProfileController:
    @http_get(
        "/{int:teacher_id}/perfil",
        response={200: AdvisorProfileOut, 401: ErrorOut, 403: ErrorOut, 404: ErrorOut},
        permissions=[IsStudent],
        summary="Perfil de um Professor do curso do Aluno",
        description=(
            "Só Professores ativos do curso do Aluno (RN28). Outro curso, Professor inativo e id "
            "desconhecido respondem o mesmo 404, para não revelar quem existe. Não traz e-mail "
            "nem matrícula. As Áreas vêm com is_common, e a compatibilidade é a da busca."
        ),
    )
    def read_profile(self, request: HttpRequest, teacher_id: int):
        return advisor_profile(request.user.student, teacher_id)
