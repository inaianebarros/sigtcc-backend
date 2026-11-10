from datetime import datetime

from django.db import transaction
from django.db.models import QuerySet

from apps.notifications.models import ADVISING_REQUEST_REFERENCE, NotificationType
from apps.notifications.services import notify

from .deadlines import answer_cutoff
from .models import AdvisingRequest, AdvisingRequestStatus


def overdue_request_ids(
    requests: QuerySet[AdvisingRequest] | None = None, cutoff: datetime | None = None
) -> list[int]:
    queryset = AdvisingRequest.objects.all() if requests is None else requests
    return list(queryset.overdue_pending(cutoff).values_list("pk", flat=True))


def expire_request(request_id: int, cutoff: datetime | None = None) -> bool:
    """Marks one overdue pending request as unanswered and tells its student (RN20, RF44, RF45).

    The only place where a request becomes unanswered, whether by the scheduled command or by the
    marking made when the student asks again, so each one gets exactly one notification, in the
    same transaction. It locks the row and checks again, so a request that someone else marked in
    the meantime is skipped. Returns whether this call marked it.
    """
    with transaction.atomic():
        advising_request = (
            AdvisingRequest.objects.select_for_update(of=("self",))
            .select_related("student__user", "teacher__user")
            .filter(pk=request_id)
            .overdue_pending(cutoff)
            .first()
        )
        if advising_request is None:
            return False
        advising_request.status = AdvisingRequestStatus.UNANSWERED
        advising_request.save(update_fields=["status"])
        notify(
            advising_request.student.user,
            NotificationType.ADVISING_REQUEST_UNANSWERED,
            reference_type=ADVISING_REQUEST_REFERENCE,
            reference_id=advising_request.pk,
            payload={"teacher_name": advising_request.teacher.user.full_name},
        )
    return True


def expire_overdue_requests(requests: QuerySet[AdvisingRequest] | None = None) -> int:
    """Expires every overdue pending request of the queryset (all of them by default)."""
    cutoff = answer_cutoff()
    return sum(expire_request(pk, cutoff) for pk in overdue_request_ids(requests, cutoff))
