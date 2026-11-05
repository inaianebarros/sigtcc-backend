from django.db import transaction

from config.errors import FieldValidationError

from .models import PublishedArticle, Student
from .profile_validation import (
    validate_area_ids,
    validate_articles,
    validate_full_name,
    validate_unknown_fields,
)
from .schemas import StudentProfileIn

STUDENT_READ_ONLY_FIELDS = frozenset(
    {"email", "password", "registration_number", "institute_id", "course_id"}
)


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
        student.articles.all().delete()
        PublishedArticle.objects.bulk_create(
            PublishedArticle(student=student, title=a.title, year=a.year, url=a.url)
            for a in data.articles or []
        )
    return student
