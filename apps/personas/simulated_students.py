import logging
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import CharField, Exists, F, OuterRef, QuerySet, Subquery, Value
from django.db.models.functions import Cast, Coalesce, Concat, Greatest, Now
from django.utils import timezone

from apps.accounts.demo_seed import DEMO_DOMAIN, INSTITUTE, OWN_COURSE
from apps.accounts.models import AreaOfInterest, Course, Student, Teacher, User
from apps.accounts.schemas import StudentRegistrationIn
from apps.accounts.services import register_student
from apps.activities.lateness import overdue_condition
from apps.activities.models import Activity, ActivityStatus, Review
from apps.activities.services import change_activity_status, create_activity, deliver_submission
from apps.advising.models import Advising, AdvisingStatus, AnswerAction
from apps.advising.services import answer_request, request_advising
from apps.advising.thesis import active_advising_of_student, write_topic

from .rules import PersonaAction, PersonaRule

logger = logging.getLogger("sigtcc.personas")

EMAIL_PREFIX = "aluno.simulado"
ACTIVE_DELIVERY_DELAY = timedelta(seconds=30)
BALANCED_DELIVERY_DELAY = timedelta(minutes=2)


@dataclass(frozen=True)
class ActivityPlan:
    title: str
    description: str
    deadline_in_days: int


@dataclass(frozen=True)
class Profile:
    slug: str
    label: str
    registration_code: str
    topic: str
    plans: tuple[ActivityPlan, ...]

    def email_for(self, teacher: Teacher) -> str:
        return f"{EMAIL_PREFIX}.{self.slug}.{teacher.user_id}@{DEMO_DOMAIN}"

    def registration_number_for(self, teacher: Teacher) -> str:
        return f"SIM{teacher.user_id}-{self.registration_code}"

    def full_name_for(self, teacher: Teacher) -> str:
        return f"Aluno Simulado {self.label} de {teacher.user.full_name.split()[0]}"


ACTIVE = Profile(
    slug="ativo",
    label="Ativo",
    registration_code="ATV",
    topic="Aluno simulado ativo: responde rápido e envia cada atividade para revisão logo.",
    plans=(
        ActivityPlan("Revisão bibliográfica", "Levantar os trabalhos relacionados ao tema.", 7),
        ActivityPlan("Capítulo de introdução", "Escrever a introdução do TCC.", 14),
        ActivityPlan("Capítulo de metodologia", "Descrever a metodologia do trabalho.", 21),
    ),
)
LATE = Profile(
    slug="atraso",
    label="em Atraso",
    registration_code="ATR",
    topic="Aluno simulado em atraso: tem prazos vencidos e nenhuma entrega.",
    plans=(
        ActivityPlan("Levantamento de requisitos", "Levantar e documentar os requisitos.", -4),
        ActivityPlan("Protótipo das telas", "Desenhar o protótipo das telas principais.", -11),
    ),
)
BALANCED = Profile(
    slug="equilibrado",
    label="Equilibrado",
    registration_code="EQL",
    topic="Aluno simulado equilibrado: entrega as atividades dentro do prazo, uma por vez.",
    plans=(
        ActivityPlan("Plano de trabalho", "Montar o plano de trabalho com o cronograma.", 5),
        ActivityPlan("Projeto da solução", "Descrever a arquitetura da solução proposta.", 12),
        ActivityPlan("Plano de testes", "Definir como a solução será validada.", 20),
    ),
)
PROFILES = (ACTIVE, LATE, BALANCED)


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


def _owned_advisings(profile: Profile) -> QuerySet[Advising]:
    """Active advisings between a real teacher and the simulated student of this profile that the
    same teacher owns: the e-mail of the student names the owner, so a simulated student never
    acts with another teacher."""
    owner_email = Concat(
        Value(f"{EMAIL_PREFIX}.{profile.slug}."),
        Cast("teacher__user_id", CharField()),
        Value(f"@{DEMO_DOMAIN}"),
    )
    return (
        Advising.objects.filter(
            status=AdvisingStatus.ACTIVE,
            student__user__is_active=True,
            teacher__user__is_active=True,
        )
        .exclude(teacher__user__email__iendswith=f"@{DEMO_DOMAIN}")
        .annotate(owner_email=owner_email)
        .filter(student__user__email__iexact=F("owner_email"))
    )


def _attachment() -> SimpleUploadedFile:
    body = b"%PDF-1.4\n% Entrega do aluno simulado\n%%EOF\n"
    return SimpleUploadedFile("entrega-simulada.pdf", body, content_type="application/pdf")


class SimulatedStudentProvisioning:
    """Gives each real teacher of the course the three simulated students, creating only the
    missing ones with the real services, as the seed does (ADR 0022).

    A student is missing when its account does not exist, never when its advising is not active:
    an advising the teacher ended is final (RN07, RN12) and is not reopened or recreated. A teacher
    that is unavailable (RN26) gets no new student, and gets it once available again."""

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
        answer_request(teacher, advising_request.pk, AnswerAction.ACCEPT)
        write_topic(active_advising_of_student(student), profile.topic)
        today = timezone.localdate()
        for plan in profile.plans:
            create_activity(
                teacher,
                student.user_id,
                plan.title,
                plan.description,
                today + timedelta(days=plan.deadline_in_days),
            )


class DeliverActivities:
    """An activity of the profile's student that is pending or in progress and not late goes to
    review through the RN15 flow: started, delivered with a minimal file and sent. A refused
    activity is back to pending, so it goes through the same flow again once the delay has passed
    since the refusal. A late activity is left alone: this student never delivers late.

    With `one_at_a_time` the student sends nothing while it already has an activity in review."""

    def __init__(
        self, name: str, profile: Profile, delay: timedelta, one_at_a_time: bool = False
    ) -> None:
        self.name = name
        self.profile = profile
        self.delay = delay
        self.one_at_a_time = one_at_a_time

    def pending_actions(self) -> Iterable[PersonaAction]:
        latest_review = (
            Review.objects.filter(activity=OuterRef("pk"))
            .order_by("-decided_at")
            .values("decided_at")[:1]
        )
        activities = (
            Activity.objects.filter(
                advising__in=_owned_advisings(self.profile),
                status__in=(ActivityStatus.PENDING, ActivityStatus.IN_PROGRESS),
                deleted_at__isnull=True,
            )
            .exclude(overdue_condition())
            .annotate(
                ready_at=Greatest("created_at", Coalesce(Subquery(latest_review), "created_at"))
            )
            .filter(ready_at__lte=Now() - self.delay)
            .select_related("advising__student__user")
        )
        if self.one_at_a_time:
            in_review = Activity.objects.filter(
                advising=OuterRef("advising"), status=ActivityStatus.IN_REVIEW
            )
            activities = activities.exclude(Exists(in_review))
            activities = activities.order_by("advising_id", "deadline", "id").distinct(
                "advising_id"
            )
        else:
            activities = activities.order_by("deadline", "id")
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


SIMULATED_STUDENT_RULES: tuple[PersonaRule, ...] = (
    SimulatedStudentProvisioning(),
    DeliverActivities("simulated_student_active_delivery", ACTIVE, ACTIVE_DELIVERY_DELAY),
    DeliverActivities(
        "simulated_student_balanced_delivery", BALANCED, BALANCED_DELIVERY_DELAY, one_at_a_time=True
    ),
)
