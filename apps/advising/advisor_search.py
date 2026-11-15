from django.contrib.postgres.lookups import Unaccent
from django.db.models import Count, IntegerField, OuterRef, Prefetch, Q, Subquery
from django.db.models.functions import Coalesce, Lower

from apps.accounts.models import AreaOfInterest, Student, Teacher, ThesisProposal

from .name_search import filter_by_name

#: How many proposals each advisor of the list shows (RF09); the rest is counted, not listed.
PROPOSALS_SHOWN = 3


def search_advisors(student: Student, name: str | None = None, *, show_all: bool):
    student_areas = list(student.areas_of_interest.values_list("id", flat=True))
    common_areas = AreaOfInterest.objects.filter(id__in=student_areas).order_by("name")
    teachers = Teacher.objects.filter(courses=student.course_id, user__is_active=True)
    teachers = filter_by_name(teachers, "user__full_name", name)
    teachers = (
        teachers.select_related("user")
        .prefetch_related(
            Prefetch("areas_of_interest", queryset=common_areas, to_attr="common_areas"),
            Prefetch(
                "areas_of_interest",
                queryset=AreaOfInterest.objects.order_by("name", "id"),
                to_attr="all_areas",
            ),
            Prefetch(
                "thesis_proposals",
                queryset=ThesisProposal.objects.order_by("-created_at", "-id")[:PROPOSALS_SHOWN],
                to_attr="latest_proposals",
            ),
        )
        .annotate(
            compatibility=Count(
                "areas_of_interest", filter=Q(areas_of_interest__in=student_areas), distinct=True
            ),
            proposals_total=Coalesce(
                Subquery(
                    ThesisProposal.objects.filter(teacher=OuterRef("pk"))
                    .values("teacher")
                    .annotate(total=Count("pk"))
                    .values("total"),
                    output_field=IntegerField(),
                ),
                0,
            ),
        )
    )
    if not show_all:
        teachers = teachers.filter(compatibility__gte=1)
    return teachers.order_by(
        "-is_available", "-compatibility", Lower(Unaccent("user__full_name")), "user_id"
    )
