from django.db.models import Prefetch
from ninja.errors import HttpError

from apps.accounts.models import AreaOfInterest, Course, PublishedArticle, Student, Teacher

from .advisor_search import search_advisors

TEACHER_NOT_FOUND_MESSAGE = "Professor não encontrado."


def advisor_profile(student: Student, teacher_id: int) -> Teacher:
    teacher = (
        # RF10: o perfil de um professor indisponível continua visível.
        search_advisors(student, only_available=False)
        .filter(user_id=teacher_id)
        .prefetch_related(
            Prefetch("courses", queryset=Course.objects.order_by("name", "id")),
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
    if teacher is None:
        raise HttpError(404, TEACHER_NOT_FOUND_MESSAGE)
    return teacher
