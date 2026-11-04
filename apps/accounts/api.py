from ninja.responses import Status
from ninja_extra import api_controller, http_get, http_post

from .models import AreaOfInterest, Course, Institute
from .schemas import (
    AreaOfInterestOut,
    CourseOut,
    InstituteOut,
    StudentOut,
    StudentRegistrationIn,
)
from .services import register_student


@api_controller("/", tags=["Catálogos"])
class CatalogController:
    @http_get("/institutos", response=list[InstituteOut], summary="Lista os institutos")
    def list_institutes(self):
        return Institute.objects.order_by("name")

    @http_get(
        "/cursos", response=list[CourseOut], summary="Lista os cursos, filtráveis por instituto"
    )
    def list_courses(self, institute_id: int | None = None):
        courses = Course.objects.order_by("name")
        if institute_id is not None:
            courses = courses.filter(institute_id=institute_id)
        return courses

    @http_get(
        "/areas-de-interesse",
        response=list[AreaOfInterestOut],
        summary="Lista as áreas de interesse",
    )
    def list_areas_of_interest(self):
        return AreaOfInterest.objects.order_by("name")


@api_controller("/auth", tags=["Autenticação"])
class RegistrationController:
    @http_post(
        "/register/aluno",
        response={201: StudentOut},
        summary="Cadastra um Aluno",
        description="O tipo de conta é implícito pela rota (aluno) e não é aceito no corpo.",
    )
    def register_student(self, payload: StudentRegistrationIn):
        student = register_student(payload)
        return Status(201, StudentOut.from_student(student))
