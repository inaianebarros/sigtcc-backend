import logging
import secrets
from collections.abc import Iterable
from datetime import timedelta

from django.db.models import Exists, F, OuterRef, QuerySet
from django.db.models.functions import Now
from django.utils import timezone

from apps.accounts.demo_seed import DEMO_DOMAIN, INSTITUTE, OWN_COURSE
from apps.accounts.models import AreaOfInterest, Course, Institute, Teacher
from apps.accounts.schemas import TeacherRegistrationIn
from apps.accounts.services import register_teacher
from apps.activities.models import Activity, ActivityStatus, Review, ReviewDecision
from apps.activities.services import create_activity, review_activity
from apps.advising.models import Advising, AdvisingRequest, AdvisingStatus, AnswerAction
from apps.advising.services import answer_request
from apps.meetings.models import Meeting, MeetingStatus
from apps.meetings.services import answer_meeting

from .rules import PersonaAction, PersonaRule

logger = logging.getLogger("sigtcc.personas")

SIMULATED_TEACHER_EMAIL = f"professor.simulado.ciencia-da-computacao@{DEMO_DOMAIN}"
SIMULATED_TEACHER_NAME = "Professor Simulado"
SIMULATED_TEACHER_REGISTRATION = "PSIMULADO01"
REQUEST_ANSWER_DELAY = timedelta(seconds=30)
ACTIVITY_CREATION_DELAY = timedelta(seconds=30)
ACTIVITY_PLAN = (
    (
        "Primeira atividade: revisar a literatura sobre o tema",
        "Atividade criada pelo professor simulado para você testar o fluxo. Mude o status para "
        "em andamento, entregue um arquivo qualquer e envie para revisão. A primeira revisão "
        "será recusada com uma justificativa padrão, e as seguintes serão aceitas.",
        7,
    ),
    (
        "Segunda atividade: escrever a introdução",
        "Atividade criada pelo professor simulado para você testar o fluxo. Entregue um "
        "arquivo qualquer e envie para revisão.",
        14,
    ),
)
FIRST_REVIEW_JUSTIFICATION = (
    "Recusa padrão do professor simulado: revise a entrega e envie novamente. "
    "As próximas revisões serão aceitas."
)


def _simulated_teacher() -> Teacher | None:
    return (
        Teacher.objects.select_related("user")
        .filter(user__email__iexact=SIMULATED_TEACHER_EMAIL, user__is_active=True)
        .first()
    )


def _only_real_students(queryset: QuerySet, student_path: str) -> QuerySet:
    """Keeps the rows whose student is an active account outside the simulation domain."""
    return queryset.filter(**{f"{student_path}__user__is_active": True}).exclude(
        **{f"{student_path}__user__email__iendswith": f"@{DEMO_DOMAIN}"}
    )


class SimulatedTeacherProvisioning:
    """Creates the simulated teacher when missing and keeps it available and visible in searches.

    It is a rule like the others, and the first one, so that a fresh environment has the teacher
    before the rules that need it read the database in the same cycle."""

    name = "simulated_teacher_provisioning"

    def pending_actions(self) -> Iterable[PersonaAction]:
        institute = Institute.objects.filter(name=INSTITUTE).first()
        course = Course.objects.filter(institute=institute, name=OWN_COURSE).first()
        area_ids = list(AreaOfInterest.objects.values_list("id", flat=True))
        if course is None or not area_ids:
            logger.warning("simulated teacher not provisioned: the course catalog is not loaded")
            return
        teacher = Teacher.objects.filter(user__email__iexact=SIMULATED_TEACHER_EMAIL).first()
        if teacher is None:
            yield self._action("create", lambda: self._create(course, area_ids))
        elif self._needs_upkeep(teacher, area_ids):
            yield self._action("update", lambda: self._keep_visible(teacher.pk, area_ids))

    @staticmethod
    def _action(step: str, perform) -> PersonaAction:
        return PersonaAction(
            SIMULATED_TEACHER_EMAIL, f"provision_simulated_teacher:{step}", perform
        )

    @staticmethod
    def _needs_upkeep(teacher: Teacher, area_ids: list[int]) -> bool:
        current = set(teacher.areas_of_interest.values_list("id", flat=True))
        return not teacher.is_available or current != set(area_ids)

    @staticmethod
    def _create(course: Course, area_ids: list[int]) -> None:
        password = secrets.token_urlsafe(32)
        register_teacher(
            TeacherRegistrationIn(
                registration_number=SIMULATED_TEACHER_REGISTRATION,
                full_name=SIMULATED_TEACHER_NAME,
                email=SIMULATED_TEACHER_EMAIL,
                password=password,
                password_confirmation=password,
                institute_id=course.institute_id,
                course_ids=[course.id],
                area_ids=area_ids,
            )
        )

    @staticmethod
    def _keep_visible(teacher_id: int, area_ids: list[int]) -> None:
        teacher = Teacher.objects.get(pk=teacher_id)
        teacher.areas_of_interest.set(area_ids)
        if not teacher.is_available:
            teacher.is_available = True
            teacher.save(update_fields=["is_available"])


class AcceptAdvisingRequests:
    """A pending request from a real student, older than the delay, is accepted (RN04 to RN06)."""

    name = "simulated_teacher_accept_advising_request"

    def pending_actions(self) -> Iterable[PersonaAction]:
        teacher = _simulated_teacher()
        if teacher is None:
            return
        requests = _only_real_students(
            AdvisingRequest.objects.filter(teacher=teacher)
            .valid_pending()
            .filter(requested_at__lte=Now() - REQUEST_ANSWER_DELAY),
            "student",
        ).order_by("requested_at", "id")
        for request_id in requests.values_list("id", flat=True):
            yield PersonaAction(
                teacher.user.email,
                f"accept_advising_request:{request_id}",
                lambda request_id=request_id: answer_request(
                    teacher, request_id, AnswerAction.ACCEPT
                ),
            )


class CreateActivities:
    """An active advising of a real student that has no activity yet, and started more than the
    delay ago, gets the planned activities, so the student can test the activity flow (RF25).

    The creation is one action, one transaction, so the advising never ends up with only part of
    the plan. Any activity of the advising, even a deleted one, counts as already planned."""

    name = "simulated_teacher_create_activities"

    def pending_actions(self) -> Iterable[PersonaAction]:
        teacher = _simulated_teacher()
        if teacher is None:
            return
        advisings = (
            _only_real_students(
                Advising.objects.filter(
                    teacher=teacher,
                    status=AdvisingStatus.ACTIVE,
                    started_at__lte=Now() - ACTIVITY_CREATION_DELAY,
                ),
                "student",
            )
            .filter(~Exists(Activity.objects.filter(advising=OuterRef("pk"))))
            .order_by("started_at", "id")
        )
        for advising_id, student_user_id in advisings.values_list("id", "student__user_id"):
            yield PersonaAction(
                teacher.user.email,
                f"create_activities:{advising_id}",
                lambda student_user_id=student_user_id: self._create_plan(teacher, student_user_id),
            )

    @staticmethod
    def _create_plan(teacher: Teacher, student_user_id: int) -> None:
        today = timezone.localdate()
        for title, description, deadline_in_days in ACTIVITY_PLAN:
            create_activity(
                teacher,
                student_user_id,
                title,
                description,
                today + timedelta(days=deadline_in_days),
            )


class ReviewActivities:
    """An activity in review of a real student's advising is refused when the advising has no
    review yet and accepted otherwise (RN21, RN40).

    Only the oldest activity in review of each advising is taken per cycle, so the decision of one
    never depends on another one of the same cycle."""

    name = "simulated_teacher_review_activity"

    def pending_actions(self) -> Iterable[PersonaAction]:
        teacher = _simulated_teacher()
        if teacher is None:
            return
        activities = (
            _only_real_students(
                Activity.objects.filter(
                    advising__teacher=teacher,
                    advising__status=AdvisingStatus.ACTIVE,
                    status=ActivityStatus.IN_REVIEW,
                    deleted_at__isnull=True,
                ),
                "advising__student",
            )
            .annotate(
                has_review=Exists(Review.objects.filter(activity__advising=OuterRef("advising")))
            )
            .order_by("advising_id", "id")
            .distinct("advising_id")
        )
        for activity in activities:
            if activity.has_review:
                decision, justification = ReviewDecision.ACCEPTED, ""
            else:
                decision, justification = ReviewDecision.REJECTED, FIRST_REVIEW_JUSTIFICATION
            yield PersonaAction(
                teacher.user.email,
                f"{decision}_activity:{activity.id}",
                lambda activity_id=activity.id, decision=decision, justification=justification: (
                    review_activity(teacher, activity_id, decision, justification)
                ),
            )


class AcceptMeetings:
    """A pending meeting the real student proposed, not over yet, is accepted (RN09 to RN11)."""

    name = "simulated_teacher_accept_meeting"

    def pending_actions(self) -> Iterable[PersonaAction]:
        teacher = _simulated_teacher()
        if teacher is None:
            return
        meetings = _only_real_students(
            Meeting.objects.filter(
                advising__teacher=teacher,
                advising__status=AdvisingStatus.ACTIVE,
                status=MeetingStatus.PENDING,
                held_at__isnull=True,
                scheduled_at__gt=Now(),
                author=F("advising__student__user"),
            ),
            "advising__student",
        ).order_by("scheduled_at", "id")
        for meeting_id in meetings.values_list("id", flat=True):
            yield PersonaAction(
                teacher.user.email,
                f"accept_meeting:{meeting_id}",
                lambda meeting_id=meeting_id: answer_meeting(
                    teacher.user, meeting_id, AnswerAction.ACCEPT, None
                ),
            )


SIMULATED_TEACHER_RULES: tuple[PersonaRule, ...] = (
    SimulatedTeacherProvisioning(),
    AcceptAdvisingRequests(),
    CreateActivities(),
    ReviewActivities(),
    AcceptMeetings(),
)
