"""The activities of the active advising of student 5, made with the real services.

Each activity is built by its own steps and skipped if its title already exists, so running
again adds nothing. An activity that needs files is skipped, with a warning, when the storage is
unavailable, and a later run completes it.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.utils import timezone
from ninja.errors import HttpError

from apps.activities.models import Activity, ActivityStatus, ReviewDecision
from apps.activities.services import (
    add_comment,
    change_activity_status,
    create_activity,
    deliver_submission,
    review_activity,
)

from .demo_scenarios import ADVISOR, COMPLETE_STUDENT
from .demo_seed import demo_email
from .models import Student, Teacher, User

PENDING_TITLE = "Revisão bibliográfica"
IN_PROGRESS_TITLE = "Capítulo 1 – Introdução"
IN_REVIEW_TITLE = "Capítulo 2 – Metodologia"
COMPLETED_TITLE = "Levantamento de requisitos"
OVERDUE_TITLE = "Protótipo da interface"
REFUSED_TITLE = "Plano de trabalho"

STORAGE_UNAVAILABLE = 503


@dataclass(frozen=True)
class Context:
    teacher: Teacher
    student: Student


@dataclass(frozen=True)
class Plan:
    title: str
    description: str
    deadline_in_days: int
    steps: Callable[[Context, Activity], None]


def _pdf(name: str, text: str) -> SimpleUploadedFile:
    body = f"%PDF-1.4\n% {text}\n%%EOF\n".encode()
    return SimpleUploadedFile(name, body, content_type="application/pdf")


def _deliver(context: Context, activity: Activity, *files: SimpleUploadedFile) -> None:
    deliver_submission(context.student, activity.pk, list(files))


def _start(context: Context, activity: Activity) -> None:
    change_activity_status(context.student, activity.pk, ActivityStatus.IN_PROGRESS)


def _send_for_review(context: Context, activity: Activity) -> None:
    change_activity_status(context.student, activity.pk, ActivityStatus.IN_REVIEW)


def _comment(user: User, activity: Activity, text: str) -> None:
    add_comment(user, activity.pk, text)


def _nothing(context: Context, activity: Activity) -> None:
    return None


def _in_progress(context: Context, activity: Activity) -> None:
    _start(context, activity)
    _comment(
        context.student.user,
        activity,
        "Comecei a escrever a introdução; segue a primeira versão em breve.",
    )
    _comment(
        context.teacher.user, activity, "Ótimo. Lembre de citar os trabalhos relacionados da área."
    )
    _comment(context.student.user, activity, "Combinado, vou incluir as referências.")


def _in_review(context: Context, activity: Activity) -> None:
    _start(context, activity)
    _deliver(
        context,
        activity,
        _pdf("metodologia-v1.pdf", "Metodologia, versão 1"),
        _pdf("fluxograma.pdf", "Fluxograma do método"),
    )
    _comment(
        context.teacher.user, activity, "Vou revisar com calma; me avise se faltar algum anexo."
    )
    _send_for_review(context, activity)


def _completed(context: Context, activity: Activity) -> None:
    _start(context, activity)
    _deliver(context, activity, _pdf("requisitos.pdf", "Levantamento de requisitos"))
    _comment(context.teacher.user, activity, "Bom levantamento, falta só confirmar com o cliente.")
    _send_for_review(context, activity)
    review_activity(context.teacher, activity.pk, ReviewDecision.ACCEPTED)


def _refused(context: Context, activity: Activity) -> None:
    _start(context, activity)
    _deliver(context, activity, _pdf("plano-v1.pdf", "Plano de trabalho, versão 1"))
    _send_for_review(context, activity)
    review_activity(
        context.teacher,
        activity.pk,
        ReviewDecision.REJECTED,
        "O cronograma não cobre a etapa de testes. Inclua as datas e reenvie.",
    )
    _comment(context.student.user, activity, "Entendido, vou incluir a etapa de testes.")


PLANS = (
    Plan(PENDING_TITLE, "Levantar os trabalhos relacionados ao tema.", 10, _nothing),
    Plan(IN_PROGRESS_TITLE, "Escrever a introdução do TCC.", 5, _in_progress),
    Plan(IN_REVIEW_TITLE, "Descrever a metodologia do trabalho.", 3, _in_review),
    Plan(COMPLETED_TITLE, "Levantar e documentar os requisitos do sistema.", -2, _completed),
    Plan(OVERDUE_TITLE, "Desenhar o protótipo das telas principais.", -3, _nothing),
    Plan(REFUSED_TITLE, "Montar o plano de trabalho com o cronograma.", 7, _refused),
)


def _build(plan: Plan, context: Context) -> None:
    deadline = timezone.localdate() + timedelta(days=plan.deadline_in_days)
    activity = create_activity(
        context.teacher, context.student.user_id, plan.title, plan.description, deadline
    )
    plan.steps(context, activity)


@transaction.atomic
def seed_activity_scenarios() -> tuple[int, list[str]]:
    """Creates the missing activities; returns how many it made and the titles it skipped."""
    context = Context(
        teacher=User.objects.get(email=demo_email(ADVISOR)).teacher,
        student=User.objects.get(email=demo_email(COMPLETE_STUDENT)).student,
    )
    created = 0
    skipped: list[str] = []
    for plan in PLANS:
        if Activity.objects.filter(advising__student=context.student, title=plan.title).exists():
            continue
        try:
            with transaction.atomic():
                _build(plan, context)
        except HttpError as error:
            if error.status_code != STORAGE_UNAVAILABLE:
                raise
            skipped.append(plan.title)
        else:
            created += 1
    return created, skipped
