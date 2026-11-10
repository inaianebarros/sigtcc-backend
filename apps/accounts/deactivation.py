from django.db import transaction
from django.db.models import Q

from apps.advising.models import (
    Advising,
    AdvisingRequest,
    AdvisingRequestStatus,
    AdvisingStatus,
)
from apps.advising.services import SYSTEM_END_REASON, close_advising
from config.errors import ConflictError

from .models import Student, User
from .sessions import revoke_user_sessions

ALREADY_DEACTIVATED_MESSAGE = "Este usuário já foi desativado."


@transaction.atomic
def deactivate_user(user: User) -> None:
    """Deactivates the account and ends every bond it still has, in one transaction (RN19).

    The deactivation is logical: the row and the e-mail stay. Any failure undoes everything.
    The caller's instance is updated too, so it does not stay stale.
    """
    locked = User.objects.select_for_update().get(pk=user.pk)
    if not locked.is_active:
        raise ConflictError(ALREADY_DEACTIVATED_MESSAGE)
    # Same order as request_advising and answer_request: the student row comes before the rest.
    Student.objects.select_for_update().filter(user=locked).first()
    locked.is_active = False
    locked.save(update_fields=["is_active"])

    _end_active_advisings(locked)
    _cancel_pending_requests(locked)
    revoke_user_sessions(locked.pk)
    user.is_active = False


def _bonds_of(user: User) -> Q:
    """Advisings and requests in which the user is the student or the teacher."""
    return Q(student__user=user) | Q(teacher__user=user)


def _end_active_advisings(user: User) -> None:
    advisings = (
        Advising.objects.select_for_update(of=("self",))
        .filter(_bonds_of(user), status=AdvisingStatus.ACTIVE)
        .order_by("pk")
    )
    for advising in advisings:
        close_advising(advising, SYSTEM_END_REASON)


def _cancel_pending_requests(user: User) -> None:
    """A pending request has no "cancelled" status: it becomes unanswered, which frees the student
    to ask again (RN05) and keeps it out of the teacher's pending list."""
    AdvisingRequest.objects.filter(_bonds_of(user), status=AdvisingRequestStatus.PENDING).update(
        status=AdvisingRequestStatus.UNANSWERED
    )
