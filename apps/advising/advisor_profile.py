from django.db.models import Prefetch
from ninja.errors import HttpError

from apps.accounts.models import Course, PublishedArticle, Student, Teacher

from .advisor_search import search_advisors

TEACHER_NOT_FOUND_MESSAGE = "Professor não encontrado."


def advisor_profile(student: Student, teacher_id: int) -> Teacher:
    teacher = (
        # RF10, RN28, RN32: o perfil de qualquer professor do curso continua visível. A busca já
        # carrega as áreas dele (`all_areas`, por nome), que o perfil reaproveita.
        search_advisors(student, show_all=True)
        .filter(user_id=teacher_id)
        .prefetch_related(
            Prefetch("courses", queryset=Course.objects.order_by("name", "id")),
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
