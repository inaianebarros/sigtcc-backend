from django.contrib.postgres.lookups import Unaccent
from django.db.models import Count, Prefetch, Q
from django.db.models.functions import Lower

from apps.accounts.models import AreaOfInterest, Student, Teacher

from .name_search import filter_by_name


def search_advisors(student: Student, name: str | None = None, *, only_available: bool):
    student_areas = list(student.areas_of_interest.values_list("id", flat=True))
    common_areas = AreaOfInterest.objects.filter(id__in=student_areas).order_by("name")
    teachers = Teacher.objects.filter(courses=student.course_id, user__is_active=True)
    if only_available:
        teachers = teachers.filter(is_available=True)
    teachers = filter_by_name(teachers, "user__full_name", name)
    return (
        teachers.select_related("user")
        .prefetch_related(
            Prefetch("areas_of_interest", queryset=common_areas, to_attr="common_areas")
        )
        .annotate(
            compatibility=Count(
                "areas_of_interest", filter=Q(areas_of_interest__in=student_areas), distinct=True
            )
        )
        .order_by("-compatibility", Lower(Unaccent("user__full_name")), "user_id")
    )
