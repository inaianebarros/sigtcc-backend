from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from apps.accounts.models import Teacher

from .deadlines import answer_deadline_of
from .models import Advising, AdvisingRequest, AdvisingRequestStatus, AdvisingStatus


class BondKind(StrEnum):
    ACTIVE_ADVISING = "active_advising"
    PENDING_REQUEST = "pending_request"
    ENDED_ADVISING = "ended_advising"
    REJECTED_REQUEST = "rejected_request"
    UNANSWERED_REQUEST = "unanswered_request"


@dataclass(frozen=True)
class Bond:
    kind: BondKind
    since: datetime
    until: datetime | None = None


def bond_of(student_id: int, teacher: Teacher) -> Bond | None:
    """The most relevant tie between a student and a teacher, or None when only an accepted
    request without an advising exists."""
    advisings = Advising.objects.filter(student_id=student_id, teacher=teacher)
    active = advisings.filter(status=AdvisingStatus.ACTIVE).first()
    if active is not None:
        return Bond(BondKind.ACTIVE_ADVISING, active.started_at)

    requests = AdvisingRequest.objects.with_effective_status().filter(
        student_id=student_id, teacher=teacher
    )
    pending = requests.filter(effective_status=AdvisingRequestStatus.PENDING).first()
    if pending is not None:
        return Bond(
            BondKind.PENDING_REQUEST, pending.requested_at, answer_deadline_of(pending.requested_at)
        )

    ended = advisings.filter(status=AdvisingStatus.ENDED).order_by("-ended_at").first()
    if ended is not None:
        return Bond(BondKind.ENDED_ADVISING, ended.started_at, ended.ended_at)

    unanswered_or_rejected = (
        requests.filter(
            effective_status__in=(AdvisingRequestStatus.REJECTED, AdvisingRequestStatus.UNANSWERED)
        )
        .order_by("-requested_at")
        .first()
    )
    if unanswered_or_rejected is None:
        return None
    if unanswered_or_rejected.effective_status == AdvisingRequestStatus.REJECTED:
        return Bond(
            BondKind.REJECTED_REQUEST,
            unanswered_or_rejected.responded_at or unanswered_or_rejected.requested_at,
        )
    return Bond(BondKind.UNANSWERED_REQUEST, unanswered_or_rejected.requested_at)
