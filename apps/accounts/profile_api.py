from django.http import HttpRequest
from ninja_extra import api_controller, http_get, http_patch
from ninja_jwt.authentication import JWTAuth

from .permissions import IsStudent, IsTeacher
from .profile import update_student_profile, update_teacher_profile
from .schemas import (
    StudentProfileIn,
    StudentProfileOut,
    TeacherProfileIn,
    TeacherProfileOut,
)


@api_controller("/alunos", tags=["Perfil"], auth=JWTAuth(), permissions=[IsStudent])
class StudentProfileController:
    @http_get("/me/perfil", response=StudentProfileOut, summary="Perfil do próprio Aluno")
    def read_profile(self, request: HttpRequest):
        return StudentProfileOut.from_student(request.user.student)

    @http_patch(
        "/me/perfil",
        response=StudentProfileOut,
        summary="Atualiza o perfil do próprio Aluno",
        description="Atualização parcial: o que não for enviado permanece como está.",
    )
    def update_profile(self, request: HttpRequest, payload: StudentProfileIn):
        student = update_student_profile(request.user.student, payload)
        return StudentProfileOut.from_student(student)


@api_controller("/professores", tags=["Perfil"], auth=JWTAuth(), permissions=[IsTeacher])
class TeacherProfileController:
    @http_get("/me/perfil", response=TeacherProfileOut, summary="Perfil do próprio Professor")
    def read_profile(self, request: HttpRequest):
        return TeacherProfileOut.from_teacher(request.user.teacher)

    @http_patch(
        "/me/perfil",
        response=TeacherProfileOut,
        summary="Atualiza o perfil do próprio Professor",
        description="Atualização parcial: o que não for enviado permanece como está.",
    )
    def update_profile(self, request: HttpRequest, payload: TeacherProfileIn):
        teacher = update_teacher_profile(request.user.teacher, payload)
        return TeacherProfileOut.from_teacher(teacher)
