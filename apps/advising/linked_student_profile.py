from django.db.models import Exists, OuterRef, Prefetch
from ninja.errors import HttpError

from apps.accounts.models import AreaOfInterest, PublishedArticle, Student, Teacher

from .models import Advising, AdvisingRequest

STUDENT_NOT_FOUND_MESSAGE = "Aluno não encontrado."


def linked_student_profile(teacher: Teacher, student_id: int) -> Student:
    sent_a_request = AdvisingRequest.objects.filter(student=OuterRef("pk"), teacher=teacher)
    has_or_had_advising = Advising.objects.filter(student=OuterRef("pk"), teacher=teacher)
    student = (
        Student.objects.filter(user_id=student_id, user__is_active=True)
        .filter(Exists(sent_a_request) | Exists(has_or_had_advising))
        .select_related("user", "course__institute")
        .prefetch_related(
            Prefetch(
                "areas_of_interest",
                queryset=AreaOfInterest.objects.order_by("name"),
                to_attr="all_areas",
            ),
            Prefetch(
                "articles",
                queryset=PublishedArticle.objects.order_by("-year", "id"),
                to_attr="ordered_articles",
            ),
        )
        .first()
    )
    if student is None:
        raise HttpError(404, STUDENT_NOT_FOUND_MESSAGE)
    teacher_areas = set(teacher.areas_of_interest.values_list("id", flat=True))
    student.common_area_ids = {area.id for area in student.all_areas} & teacher_areas
    return student
