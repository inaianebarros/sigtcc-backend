from django.db import transaction

from config.errors import FieldValidationError

from .models import Student, Teacher, normalize_title
from .profile_validation import (
    validate_area_ids,
    validate_articles,
    validate_course_ids,
    validate_full_name,
    validate_unknown_fields,
)
from .schemas import ArticleIn, ProfileChangesIn, StudentProfileIn, TeacherProfileIn

ProfileOwner = Student | Teacher
COMMON_READ_ONLY_FIELDS = frozenset({"email", "password", "registration_number", "institute_id"})
STUDENT_READ_ONLY_FIELDS = COMMON_READ_ONLY_FIELDS | {"course_id"}
TEACHER_READ_ONLY_FIELDS = COMMON_READ_ONLY_FIELDS
OUTSIDE_INSTITUTE_MESSAGE = "Todos os cursos devem pertencer ao seu instituto."


def replace_articles(owner: ProfileOwner, articles: list[ArticleIn]) -> None:
    """Sincroniza os artigos do dono com a lista enviada, mantendo o id dos que continuam."""
    existing = {(a.title_key, a.year): a for a in owner.articles.all()}
    incoming = {(normalize_title(a.title), a.year): a for a in articles}

    for key, article in existing.items():
        if key not in incoming:
            article.delete()
    for key, data in incoming.items():
        if key in existing:
            article = existing[key]
            article.title, article.url = data.title, data.url
            article.save()
        else:
            owner.articles.create(title=data.title, year=data.year, url=data.url)


def _validate_common_changes(
    data: ProfileChangesIn, read_only_fields: frozenset[str]
) -> dict[str, str]:
    sent = data.model_fields_set
    errors = validate_unknown_fields(data.model_extra or {}, read_only_fields)
    if "full_name" in sent:
        errors.update(validate_full_name(data.full_name or ""))
    if "area_ids" in sent:
        errors.update(validate_area_ids(data.area_ids or []))
    if "articles" in sent:
        errors.update(validate_articles(data.articles))
    return errors


def _apply_common_changes(owner: ProfileOwner, data: ProfileChangesIn) -> None:
    sent = data.model_fields_set
    if "full_name" in sent:
        owner.user.full_name = data.full_name
        owner.user.save(update_fields=["full_name"])
    if "area_ids" in sent:
        owner.areas_of_interest.set(data.area_ids or [])
    if "articles" in sent:
        replace_articles(owner, data.articles or [])


@transaction.atomic
def update_student_profile(student: Student, data: StudentProfileIn) -> Student:
    student = Student.objects.select_for_update().select_related("user").get(pk=student.pk)

    errors = _validate_common_changes(data, STUDENT_READ_ONLY_FIELDS)
    if errors:
        raise FieldValidationError(errors)

    _apply_common_changes(student, data)
    return student


@transaction.atomic
def update_teacher_profile(teacher: Teacher, data: TeacherProfileIn) -> Teacher:
    teacher = Teacher.objects.select_for_update().select_related("user").get(pk=teacher.pk)
    sent = data.model_fields_set

    errors = _validate_common_changes(data, TEACHER_READ_ONLY_FIELDS)
    if "course_ids" in sent:
        institute_id = teacher.courses.values_list("institute_id", flat=True).first()
        errors.update(
            validate_course_ids(data.course_ids or [], institute_id, OUTSIDE_INSTITUTE_MESSAGE)
        )
    if "is_available" in sent and data.is_available is None:
        errors["is_available"] = "Informe se você está disponível para orientar."
    if errors:
        raise FieldValidationError(errors)

    _apply_common_changes(teacher, data)
    if "course_ids" in sent:
        teacher.courses.set(data.course_ids or [])
    if "is_available" in sent:
        teacher.is_available = data.is_available
        teacher.save(update_fields=["is_available"])
    return teacher
