from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

from config.errors import FieldValidationError

from .models import AreaOfInterest, Course, Institute, Student, UserType
from .schemas import StudentRegistrationIn

User = get_user_model()


def _validate_credentials(data: StudentRegistrationIn) -> dict[str, str]:
    errors: dict[str, str] = {}
    try:
        validate_email(data.email)
    except ValidationError:
        errors["email"] = "Informe um e-mail válido."
    else:
        if User.objects.filter(email__iexact=data.email).exists():
            errors["email"] = "Já existe uma conta com este e-mail."

    if data.password != data.password_confirmation:
        errors["password_confirmation"] = "As senhas não coincidem."

    try:
        validate_password(data.password, User(email=data.email, full_name=data.full_name))
    except ValidationError as error:
        errors["password"] = " ".join(error.messages)
    return errors


def _validate_profile(data: StudentRegistrationIn) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not data.full_name.strip():
        errors["full_name"] = "Informe o nome completo."

    registration_number = data.registration_number.strip()
    if not registration_number:
        errors["registration_number"] = "Informe a matrícula."
    elif Student.objects.filter(registration_number=registration_number).exists():
        errors["registration_number"] = "Já existe um aluno com esta matrícula."

    institute_exists = Institute.objects.filter(id=data.institute_id).exists()
    if not institute_exists:
        errors["institute_id"] = "Instituto não encontrado."

    course = Course.objects.filter(id=data.course_id).first()
    if course is None:
        errors["course_id"] = "Curso não encontrado."
    elif institute_exists and course.institute_id != data.institute_id:
        errors["course_id"] = "O curso não pertence ao instituto informado."

    area_ids = set(data.area_ids)
    if not area_ids:
        errors["area_ids"] = "Escolha ao menos uma área de interesse."
    elif AreaOfInterest.objects.filter(id__in=area_ids).count() != len(area_ids):
        errors["area_ids"] = "Área de interesse não encontrada."
    return errors


@transaction.atomic
def register_student(data: StudentRegistrationIn) -> Student:
    errors = {**_validate_credentials(data), **_validate_profile(data)}
    if errors:
        raise FieldValidationError(errors)

    user = User.objects.create_user(
        email=data.email,
        password=data.password,
        full_name=data.full_name.strip(),
        type=UserType.STUDENT,
    )
    student = Student.objects.create(
        user=user,
        course_id=data.course_id,
        registration_number=data.registration_number.strip(),
    )
    student.areas_of_interest.set(data.area_ids)
    return student
