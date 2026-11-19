import logging
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import CharField, Exists, F, OuterRef, Q, QuerySet, Subquery, Value
from django.db.models.functions import Cast, Coalesce, Concat, Greatest, Now
from django.utils import timezone

from apps.accounts.demo_seed import DEMO_DOMAIN, INSTITUTE, OWN_COURSE
from apps.accounts.models import AreaOfInterest, Course, Student, Teacher, User
from apps.accounts.schemas import StudentRegistrationIn
from apps.accounts.services import register_student
from apps.activities.lateness import overdue_condition
from apps.activities.models import Activity, ActivityStatus, Review
from apps.activities.services import change_activity_status, create_activity, deliver_submission
from apps.advising.deadlines import ANSWER_DEADLINE
from apps.advising.models import (
    Advising,
    AdvisingRequest,
    AdvisingRequestStatus,
    AdvisingStatus,
    AnswerAction,
)
from apps.advising.services import answer_request, request_advising
from apps.advising.thesis import active_advising_of_student, write_topic
from apps.meetings.models import Meeting, MeetingStatus
from apps.meetings.services import answer_meeting

from .rules import PersonaAction, PersonaRule

logger = logging.getLogger("sigtcc.personas")

EMAIL_PREFIX = "aluno.simulado"
DELIVERY_DELAY = timedelta(seconds=30)
REQUEST_AGAIN_DELAY = timedelta(minutes=2)
LINKED_TOPIC = "Aluno simulado vinculado: entrega as atividades no prazo e aceita as reuniões."
MEETING_EXCUSES = (
    "Tenho prova nesse horário e não consigo participar.",
    "Estou com um compromisso que não posso remarcar nesse horário.",
    "Nesse horário estou em aula e não consigo me ausentar.",
    "Surgiu um imprevisto pessoal e não poderei comparecer nesse horário.",
)


@dataclass(frozen=True)
class ActivityPlan:
    title: str
    description: str
    deadline_in_days: int


LINKED_PLANS = (
    ActivityPlan("Revisão bibliográfica", "Levantar os trabalhos relacionados ao tema.", 7),
    ActivityPlan("Capítulo de introdução", "Escrever a introdução do TCC.", 14),
    ActivityPlan("Levantamento de requisitos", "Levantar e documentar os requisitos.", -4),
)


@dataclass(frozen=True)
class Profile:
    slug: str
    label: str
    registration_code: str
    starts_linked: bool

    def email_for(self, teacher: Teacher) -> str:
        return f"{EMAIL_PREFIX}.{self.slug}.{teacher.user_id}@{DEMO_DOMAIN}"

    def registration_number_for(self, teacher: Teacher) -> str:
        return f"SIM{teacher.user_id}-{self.registration_code}"

    def full_name_for(self, teacher: Teacher) -> str:
        return f"Aluno Simulado {self.label} de {teacher.user.full_name.split()[0]}"


LINKED = Profile(slug="vinculado", label="Vinculado", registration_code="VNC", starts_linked=True)
REQUESTER = Profile(
    slug="solicitante", label="Solicitante", registration_code="SOL", starts_linked=False
)
PROFILES = (LINKED, REQUESTER)


def _real_teachers() -> QuerySet[Teacher]:
    """Active teachers of the course outside the simulation domain (ADR 0022)."""
    return (
        Teacher.objects.select_related("user")
        .filter(
            user__is_active=True,
            courses__name=OWN_COURSE,
            courses__institute__name=INSTITUTE,
        )
        .exclude(user__email__iendswith=f"@{DEMO_DOMAIN}")
        .order_by("user_id")
    )


def _owner_email(profile: Profile, teacher_id_path: str) -> Concat:
    return Concat(
        Value(f"{EMAIL_PREFIX}.{profile.slug}."),
        Cast(teacher_id_path, CharField()),
        Value(f"@{DEMO_DOMAIN}"),
    )


def _owned_advisings(profile: Profile) -> QuerySet[Advising]:
    """Active advisings between a real teacher and the simulated student of this profile that the
    same teacher owns: the e-mail of the student names the owner, so a simulated student never
    acts with another teacher."""
    return (
        Advising.objects.filter(
            status=AdvisingStatus.ACTIVE,
            student__user__is_active=True,
            teacher__user__is_active=True,
        )
        .exclude(teacher__user__email__iendswith=f"@{DEMO_DOMAIN}")
        .annotate(owner_email=_owner_email(profile, "teacher__user_id"))
        .filter(student__user__email__iexact=F("owner_email"))
    )


def _attachment() -> SimpleUploadedFile:
    body = b"%PDF-1.4\n% Entrega do aluno simulado\n%%EOF\n"
    return SimpleUploadedFile("entrega-simulada.pdf", body, content_type="application/pdf")


class SimulatedStudentProvisioning:
    """Gives each real teacher of the course the linked and the requester simulated students,
    creating only the missing ones with the real services, as the seed does (ADR 0022).

    A student is missing when its account does not exist, never when its advising or request is
    not active: an advising the teacher ended is final (RN07, RN12) and is not reopened or
    recreated. A teacher that is unavailable (RN26) gets no new student, and gets it once
    available again."""

    name = "simulated_students_provisioning"

    def pending_actions(self) -> Iterable[PersonaAction]:
        course = Course.objects.filter(institute__name=INSTITUTE, name=OWN_COURSE).first()
        catalog_area_ids = list(AreaOfInterest.objects.values_list("id", flat=True)[:1])
        if course is None or not catalog_area_ids:
            logger.warning("simulated students not provisioned: the course catalog is not loaded")
            return
        existing = {
            email.lower()
            for email in User.objects.filter(
                email__istartswith=f"{EMAIL_PREFIX}.", email__iendswith=f"@{DEMO_DOMAIN}"
            ).values_list("email", flat=True)
        }
        for teacher in _real_teachers().filter(is_available=True).distinct():
            for profile in PROFILES:
                email = profile.email_for(teacher)
                if email not in existing:
                    yield PersonaAction(
                        email,
                        f"provision_simulated_student:{profile.slug}:{teacher.user_id}",
                        lambda teacher=teacher, profile=profile: self._create(
                            teacher, profile, course, catalog_area_ids
                        ),
                    )

    @staticmethod
    def _create(
        teacher: Teacher, profile: Profile, course: Course, fallback_area_ids: list[int]
    ) -> None:
        password = secrets.token_urlsafe(32)
        area_ids = list(teacher.areas_of_interest.values_list("id", flat=True)) or fallback_area_ids
        student = register_student(
            StudentRegistrationIn(
                registration_number=profile.registration_number_for(teacher),
                full_name=profile.full_name_for(teacher),
                email=profile.email_for(teacher),
                password=password,
                password_confirmation=password,
                institute_id=course.institute_id,
                course_id=course.id,
                area_ids=area_ids,
            )
        )
        advising_request = request_advising(student, teacher.user_id)
        if not profile.starts_linked:
            return
        answer_request(teacher, advising_request.pk, AnswerAction.ACCEPT)
        write_topic(active_advising_of_student(student), LINKED_TOPIC)
        today = timezone.localdate()
        for plan in LINKED_PLANS:
            create_activity(
                teacher,
                student.user_id,
                plan.title,
                plan.description,
                today + timedelta(days=plan.deadline_in_days),
            )


class DeliverActivities:
    """An activity of the linked student that is pending or in progress and not late goes to
    review through the RN15 flow: started, delivered with a minimal file and sent. A refused
    activity is back to pending, so it goes through the same flow again once the delay has passed
    since the refusal. A late activity is left alone: this student never delivers late."""

    name = "simulated_linked_student_delivery"

    def pending_actions(self) -> Iterable[PersonaAction]:
        latest_review = (
            Review.objects.filter(activity=OuterRef("pk"))
            .order_by("-decided_at")
            .values("decided_at")[:1]
        )
        activities = (
            Activity.objects.filter(
                advising__in=_owned_advisings(LINKED),
                status__in=(ActivityStatus.PENDING, ActivityStatus.IN_PROGRESS),
                deleted_at__isnull=True,
            )
            .exclude(overdue_condition())
            .annotate(
                ready_at=Greatest("created_at", Coalesce(Subquery(latest_review), "created_at"))
            )
            .filter(ready_at__lte=Now() - DELIVERY_DELAY)
            .select_related("advising__student__user")
            .order_by("deadline", "id")
        )
        for activity in activities:
            student = activity.advising.student
            yield PersonaAction(
                student.user.email,
                f"send_activity_for_review:{activity.id}",
                lambda student=student, activity_id=activity.id: self._send_for_review(
                    student, activity_id
                ),
            )

    @staticmethod
    def _send_for_review(student: Student, activity_id: int) -> None:
        change_activity_status(student, activity_id, ActivityStatus.IN_PROGRESS)
        deliver_submission(student, activity_id, [_attachment()])
        change_activity_status(student, activity_id, ActivityStatus.IN_REVIEW)


def _meetings_proposed_by_the_teacher(profile: Profile) -> QuerySet[Meeting]:
    """Pending meetings, not over yet, that the real teacher proposed to the student of the
    profile (RN09 to RN11)."""
    return (
        Meeting.objects.filter(
            advising__in=_owned_advisings(profile),
            status=MeetingStatus.PENDING,
            scheduled_at__gt=Now(),
            author=F("advising__teacher__user"),
        )
        .select_related("advising__student__user")
        .order_by("scheduled_at", "id")
    )


class AcceptTeacherMeetings:
    """The linked student always accepts a meeting the real teacher proposed."""

    name = "simulated_linked_student_accept_meeting"

    def pending_actions(self) -> Iterable[PersonaAction]:
        for meeting in _meetings_proposed_by_the_teacher(LINKED):
            student_user = meeting.advising.student.user
            yield PersonaAction(
                student_user.email,
                f"accept_meeting:{meeting.id}",
                lambda user=student_user, meeting_id=meeting.id: answer_meeting(
                    user, meeting_id, AnswerAction.ACCEPT, None
                ),
            )


class RejectTeacherMeetings:
    """The requester student always rejects a meeting the real teacher proposed, with an excuse
    picked by the meeting id so that the same meeting always gets the same one."""

    name = "simulated_requester_student_reject_meeting"

    def pending_actions(self) -> Iterable[PersonaAction]:
        for meeting in _meetings_proposed_by_the_teacher(REQUESTER):
            student_user = meeting.advising.student.user
            excuse = MEETING_EXCUSES[meeting.id % len(MEETING_EXCUSES)]
            yield PersonaAction(
                student_user.email,
                f"reject_meeting:{meeting.id}",
                lambda user=student_user, meeting_id=meeting.id, excuse=excuse: answer_meeting(
                    user, meeting_id, AnswerAction.REJECT, excuse
                ),
            )


class RequestAdvisingAgain:
    """The requester student asks the same teacher again once its latest request was rejected or
    expired (RN05, RN20) and the delay has passed since then.

    It never asks again after an advising with that teacher ended (RN07, RN12), and only when it
    has no active advising and no pending request. A teacher that became unavailable (RN26) makes
    the service refuse the request, which the cycle counts as skipped."""

    name = "simulated_requester_student_request_again"

    def pending_actions(self) -> Iterable[PersonaAction]:
        now = Now()
        was_rejected = Q(
            status=AdvisingRequestStatus.REJECTED, responded_at__lte=now - REQUEST_AGAIN_DELAY
        )
        expired = Q(
            status__in=(AdvisingRequestStatus.PENDING, AdvisingRequestStatus.UNANSWERED),
            requested_at__lte=now - ANSWER_DEADLINE - REQUEST_AGAIN_DELAY,
        )
        newer_request = AdvisingRequest.objects.filter(
            student=OuterRef("student"), pk__gt=OuterRef("pk")
        )
        advising_of_the_student = Advising.objects.filter(student=OuterRef("student"))
        active_advising = advising_of_the_student.filter(status=AdvisingStatus.ACTIVE)
        ended_with_the_teacher = advising_of_the_student.filter(
            teacher=OuterRef("teacher"), status=AdvisingStatus.ENDED
        )
        requests = (
            AdvisingRequest.objects.filter(
                was_rejected | expired,
                student__user__is_active=True,
                teacher__user__is_active=True,
            )
            .exclude(teacher__user__email__iendswith=f"@{DEMO_DOMAIN}")
            .annotate(owner_email=_owner_email(REQUESTER, "teacher__user_id"))
            .filter(student__user__email__iexact=F("owner_email"))
            .exclude(Exists(newer_request))
            .exclude(Exists(active_advising))
            .exclude(Exists(ended_with_the_teacher))
            .select_related("student__user", "teacher")
            .order_by("id")
        )
        for advising_request in requests:
            student = advising_request.student
            teacher_id = advising_request.teacher.user_id
            yield PersonaAction(
                student.user.email,
                f"request_advising_again:{teacher_id}",
                lambda student=student, teacher_id=teacher_id: request_advising(
                    student, teacher_id
                ),
            )


SIMULATED_STUDENT_RULES: tuple[PersonaRule, ...] = (
    SimulatedStudentProvisioning(),
    DeliverActivities(),
    AcceptTeacherMeetings(),
    RejectTeacherMeetings(),
    RequestAdvisingAgain(),
)
