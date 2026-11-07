from django.http import HttpRequest
from ninja import Schema
from ninja_extra import api_controller, http_get
from ninja_jwt.authentication import JWTAuth

from apps.accounts.permissions import IsTeacher
from apps.accounts.schemas import ArticleOut, InstituteOut
from config.schemas import ErrorOut

from .linked_student_profile import linked_student_profile
from .schemas import CourseSummaryOut, ProfileAreaOut


class LinkedStudentProfileOut(Schema):
    id: int
    full_name: str
    institute: InstituteOut
    course: CourseSummaryOut
    areas: list[ProfileAreaOut]
    compatibility: int
    articles: list[ArticleOut]

    @staticmethod
    def resolve_id(obj) -> int:
        return obj.user_id

    @staticmethod
    def resolve_full_name(obj) -> str:
        return obj.user.full_name

    @staticmethod
    def resolve_institute(obj) -> InstituteOut:
        institute = obj.course.institute
        return InstituteOut(id=institute.id, name=institute.name)

    @staticmethod
    def resolve_areas(obj) -> list[ProfileAreaOut]:
        return ProfileAreaOut.mark_common(obj.all_areas, obj.common_area_ids)

    @staticmethod
    def resolve_compatibility(obj) -> int:
        return len(obj.common_area_ids)

    @staticmethod
    def resolve_articles(obj) -> list[ArticleOut]:
        return [ArticleOut.from_article(article) for article in obj.ordered_articles]


@api_controller("/alunos", tags=["Orientandos"], auth=JWTAuth())
class LinkedStudentProfileController:
    @http_get(
        "/{int:student_id}/perfil",
        response={200: LinkedStudentProfileOut, 401: ErrorOut, 403: ErrorOut, 404: ErrorOut},
        permissions=[IsTeacher],
        summary="Perfil de um Aluno vinculado ao Professor",
        description=(
            "Só Alunos que enviaram uma solicitação ao Professor, em qualquer status, ou que "
            "têm ou tiveram orientação com ele (RN32). Qualquer outro Aluno, Aluno inativo e id "
            "desconhecido respondem o mesmo 404, para não revelar quem existe. Não traz e-mail "
            "nem matrícula. As Áreas vêm com is_common, e a compatibilidade é a da busca."
        ),
    )
    def read_profile(self, request: HttpRequest, student_id: int):
        return linked_student_profile(request.user.teacher, student_id)
