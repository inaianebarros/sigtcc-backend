"""The scenarios of the demonstration data, made with the real services of the application.

Each scenario runs only for a student that still has nothing of its kind, so running again adds
nothing. Dates are relative to the day of the run.
"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.advising.deadlines import ANSWER_DEADLINE
from apps.advising.expiration import expire_request
from apps.advising.models import Advising, AdvisingRequest, AnswerAction
from apps.advising.services import answer_request, request_advising

from .demo_seed import demo_email
from .models import Student, Teacher, User

ADVISOR = "prof1.orientandos"
PENDING_STUDENT = "aluno2.solicitacao-pendente"
REFUSED_STUDENT = "aluno3.solicitacao-recusada"
UNANSWERED_STUDENT = "aluno4.solicitacao-nao-respondida"
COMPLETE_STUDENT = "aluno5.orientacao-completa"
EMPTY_STUDENT = "aluno6.orientacao-vazia"

# Relative to the seven-day deadline (RN20), so the scenarios follow it if the rule changes.
PENDING_AGE = ANSWER_DEADLINE - timedelta(days=1)
REFUSED_AGE = ANSWER_DEADLINE + timedelta(days=5)
UNANSWERED_AGE = ANSWER_DEADLINE + timedelta(days=2)
RESPONSE_DELAY = timedelta(days=1)
COMPLETE_ADVISING_AGE = timedelta(days=30)
EMPTY_ADVISING_AGE = timedelta(days=20)
ADVISING_AGES = {COMPLETE_STUDENT: COMPLETE_ADVISING_AGE, EMPTY_STUDENT: EMPTY_ADVISING_AGE}


def _student(slug: str) -> Student:
    return User.objects.get(email=demo_email(slug)).student


def _advisor() -> Teacher:
    return User.objects.get(email=demo_email(ADVISOR)).teacher


def _has_requests(student: Student) -> bool:
    return AdvisingRequest.objects.filter(student=student).exists()


def _send_request(student: Student, advisor: Teacher, age: timedelta) -> AdvisingRequest:
    """Sends the request through the service, then makes it `age` old."""
    advising_request = request_advising(student, advisor.user_id)
    AdvisingRequest.objects.filter(pk=advising_request.pk).update(requested_at=timezone.now() - age)
    return advising_request


@transaction.atomic
def seed_request_scenarios() -> int:
    """Students 1 to 4 and teacher 1: none, pending, refused and unanswered requests."""
    advisor = _advisor()
    created = 0

    pending = _student(PENDING_STUDENT)
    if not _has_requests(pending):
        _send_request(pending, advisor, PENDING_AGE)
        created += 1

    refused = _student(REFUSED_STUDENT)
    if not _has_requests(refused):
        # Answered while it is new (a request over 7 days cannot be answered), then aged.
        advising_request = request_advising(refused, advisor.user_id)
        answer_request(advisor, advising_request.pk, AnswerAction.REJECT)
        AdvisingRequest.objects.filter(pk=advising_request.pk).update(
            requested_at=timezone.now() - REFUSED_AGE,
            responded_at=timezone.now() - REFUSED_AGE + RESPONSE_DELAY,
        )
        created += 1

    unanswered = _student(UNANSWERED_STUDENT)
    if not _has_requests(unanswered):
        advising_request = _send_request(unanswered, advisor, UNANSWERED_AGE)
        expire_request(advising_request.pk)
        created += 1

    return created


@transaction.atomic
def seed_active_advisings() -> int:
    """Students 5 and 6 get an active advising with teacher 1, through request and acceptance."""
    advisor = _advisor()
    created = 0
    for slug, age in ADVISING_AGES.items():
        student = _student(slug)
        if Advising.objects.filter(student=student).exists():
            continue
        advising_request = request_advising(student, advisor.user_id)
        answer_request(advisor, advising_request.pk, AnswerAction.ACCEPT)
        started = timezone.now() - age
        Advising.objects.filter(request=advising_request).update(started_at=started)
        AdvisingRequest.objects.filter(pk=advising_request.pk).update(
            requested_at=started - RESPONSE_DELAY, responded_at=started
        )
        created += 1
    return created
