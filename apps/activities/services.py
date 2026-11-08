from datetime import date

from django.db import transaction
from ninja.errors import HttpError

from apps.accounts.models import Teacher
from apps.advising.linked_student_profile import STUDENT_NOT_FOUND_MESSAGE
from apps.advising.models import Advising, AdvisingStatus

from .models import Activity


@transaction.atomic
def create_activity(
    teacher: Teacher, student_id: int, title: str, description: str, deadline: date
) -> Activity:
    """Creates an activity for the active advising between the teacher and the student (RN13).

    The advising row is locked by the same query that checks it is active (ADR 0004), so an
    advising that ends meanwhile is answered with the same 404 and nothing is created.
    """
    advising = (
        Advising.objects.select_for_update()
        .filter(student__user_id=student_id, teacher=teacher, status=AdvisingStatus.ACTIVE)
        .first()
    )
    if advising is None:
        raise HttpError(404, STUDENT_NOT_FOUND_MESSAGE)
    return Activity.objects.create(
        advising=advising, title=title, description=description, deadline=deadline
    )
