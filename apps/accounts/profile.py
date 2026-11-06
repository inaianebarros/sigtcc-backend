from django.db import transaction

from config.errors import FieldValidationError

from .models import Student, normalize_title
from .profile_validation import (
    validate_area_ids,
    validate_articles,
    validate_full_name,
    validate_unknown_fields,
)
from .schemas import ArticleIn, StudentProfileIn

STUDENT_READ_ONLY_FIELDS = frozenset(
    {"email", "password", "registration_number", "institute_id", "course_id"}
)


def replace_articles(owner: Student, articles: list[ArticleIn]) -> None:
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


@transaction.atomic
def update_student_profile(student: Student, data: StudentProfileIn) -> Student:
    student = Student.objects.select_for_update().select_related("user").get(pk=student.pk)
    sent = data.model_fields_set

    errors = validate_unknown_fields(data.model_extra or {}, STUDENT_READ_ONLY_FIELDS)
    if "full_name" in sent:
        errors.update(validate_full_name(data.full_name or ""))
    if "area_ids" in sent:
        errors.update(validate_area_ids(data.area_ids or []))
    if "articles" in sent:
        errors.update(validate_articles(data.articles))
    if errors:
        raise FieldValidationError(errors)

    if "full_name" in sent:
        student.user.full_name = data.full_name
        student.user.save(update_fields=["full_name"])
    if "area_ids" in sent:
        student.areas_of_interest.set(data.area_ids or [])
    if "articles" in sent:
        replace_articles(student, data.articles or [])
    return student
