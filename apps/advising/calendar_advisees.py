from django.contrib.postgres.lookups import Unaccent
from django.db.models import Exists, OuterRef, QuerySet
from django.db.models.functions import Lower

from apps.accounts.models import Student, Teacher

from .models import Advising, AdvisingStatus
from .name_search import filter_by_name


def list_calendar_advisees(teacher: Teacher, search: str | None = None) -> QuerySet[Student]:
    """The students of the teacher's advisings, active or ended, for the calendar panel (RF59).

    A student who had more than one advising with the teacher appears once, and is `ended` only when
    none of them is active. Students of other teachers, or without any advising with this one, and
    inactive accounts never appear (RN32). Ordered by name without accents, then id.
    """
    with_teacher = Advising.objects.filter(student=OuterRef("pk"), teacher=teacher)
    students = (
        Student.objects.filter(user__is_active=True)
        .filter(Exists(with_teacher))
        .select_related("user")
        .annotate(has_active=Exists(with_teacher.filter(status=AdvisingStatus.ACTIVE)))
    )
    students = filter_by_name(students, "user__full_name", search)
    return students.order_by(Lower(Unaccent("user__full_name")), "user_id")
