from apps.accounts.models import UserType

from .models import Advising, AdvisingStatus


def advising_state_of(user) -> dict[str, bool | int]:
    """What the session needs to know about the user's advisings (RN29); empty for others."""
    active = Advising.objects.filter(status=AdvisingStatus.ACTIVE)
    match user.type:
        case UserType.STUDENT:
            return {"has_active_advising": active.filter(student__user_id=user.id).exists()}
        case UserType.TEACHER:
            return {"active_advisees_count": active.filter(teacher__user_id=user.id).count()}
        case _:
            return {}
