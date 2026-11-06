from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import models, transaction

from config.errors import FieldValidationError

from .models import Course, Institute, Student, Teacher, UserType
from .passwords import validate_new_password
from .profile_validation import validate_area_ids, validate_course_ids, validate_full_name
from .schemas import ProfileRegistrationIn, StudentRegistrationIn, TeacherRegistrationIn

User = get_user_model()


def _max_length(model: type[models.Model], field: str) -> int:
    return model._meta.get_field(field).max_length


def _validate_account(data: ProfileRegistrationIn) -> dict[str, str]:
    errors = validate_full_name(data.full_name)

    try:
        validate_email(data.email)
    except ValidationError:
        errors["email"] = "Informe um e-mail válido."
    else:
        if len(data.email) > _max_length(User, "email"):
            errors["email"] = (
                f"O e-mail deve ter no máximo {_max_length(User, 'email')} caracteres."
            )
        elif User.objects.filter(email__iexact=data.email).exists():
            errors["email"] = "Já existe uma conta com este e-mail."

    errors.update(
        validate_new_password(
            data.password,
            data.password_confirmation,
            User(email=data.email, full_name=data.full_name),
        )
    )
    return errors


def _validate_registration_number(
    data: ProfileRegistrationIn, profile_model: type[models.Model]
) -> dict[str, str]:
    max_length = _max_length(profile_model, "registration_number")
    if not data.registration_number:
        return {"registration_number": "Informe a matrícula."}
    if len(data.registration_number) > max_length:
        return {"registration_number": f"A matrícula deve ter no máximo {max_length} caracteres."}
    if profile_model.objects.filter(registration_number=data.registration_number).exists():
        return {"registration_number": "Já existe um cadastro com esta matrícula."}
    return {}


def _validate_institute_and_areas(data: ProfileRegistrationIn) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not Institute.objects.filter(id=data.institute_id).exists():
        errors["institute_id"] = "Instituto não encontrado."

    errors.update(validate_area_ids(data.area_ids))
    return errors


def _validate_course(data: StudentRegistrationIn) -> dict[str, str]:
    course = Course.objects.filter(id=data.course_id).first()
    if course is None:
        return {"course_id": "Curso não encontrado."}
    if course.institute_id != data.institute_id:
        return {"course_id": "O curso não pertence ao instituto informado."}
    return {}


def _validate_courses(data: TeacherRegistrationIn) -> dict[str, str]:
    return validate_course_ids(
        data.course_ids,
        data.institute_id,
        "Todos os cursos devem pertencer ao instituto informado.",
    )


def _create_user(data: ProfileRegistrationIn, user_type: UserType):
    return User.objects.create_user(
        email=data.email,
        password=data.password,
        full_name=data.full_name,
        type=user_type,
    )


@transaction.atomic
def register_student(data: StudentRegistrationIn) -> Student:
    errors = {
        **_validate_account(data),
        **_validate_registration_number(data, Student),
        **_validate_institute_and_areas(data),
        **_validate_course(data),
    }
    if errors:
        raise FieldValidationError(errors)

    student = Student.objects.create(
        user=_create_user(data, UserType.STUDENT),
        course_id=data.course_id,
        registration_number=data.registration_number,
    )
    student.areas_of_interest.set(data.area_ids)
    return student


@transaction.atomic
def register_teacher(data: TeacherRegistrationIn) -> Teacher:
    errors = {
        **_validate_account(data),
        **_validate_registration_number(data, Teacher),
        **_validate_institute_and_areas(data),
        **_validate_courses(data),
    }
    if errors:
        raise FieldValidationError(errors)

    teacher = Teacher.objects.create(
        user=_create_user(data, UserType.TEACHER),
        registration_number=data.registration_number,
    )
    teacher.courses.set(data.course_ids)
    teacher.areas_of_interest.set(data.area_ids)
    return teacher
