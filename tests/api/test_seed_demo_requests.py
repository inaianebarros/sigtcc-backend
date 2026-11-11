"""The request scenarios of the demonstration data: students 1 to 4 and teacher 1 (#139)."""

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.accounts.demo_seed import DEMO_DOMAIN
from apps.accounts.models import User
from apps.advising.models import AdvisingRequest, AdvisingRequestStatus
from apps.notifications.models import Notification, NotificationType
from tests.api.helpers import bearer

pytestmark = pytest.mark.django_db

STATUS_URL = "/api/orientacao/minha-solicitacao"
RECEIVED_URL = "/api/orientacao/solicitacoes"
NO_REQUEST = "aluno1.sem-orientacao"
PENDING = "aluno2.solicitacao-pendente"
REFUSED = "aluno3.solicitacao-recusada"
UNANSWERED = "aluno4.solicitacao-nao-respondida"
TEACHER = "prof1.orientandos"
ACCEPTED = [
    "aluno5.orientacao-completa",
    "aluno6.orientacao-vazia",
    "aluno7.orientacao-encerrada",
]
WITHOUT_REQUESTS = ["aluno8.outro-curso"]


def user_of(slug):
    return User.objects.get(email=f"{slug}@{DEMO_DOMAIN}")


def seed():
    call_command("seed_demo_data", stdout=StringIO())


def status_of(client, slug):
    return client.get(STATUS_URL, headers=bearer(user_of(slug))).json()


def received_by_teacher(client):
    response = client.get(RECEIVED_URL, headers=bearer(user_of(TEACHER)))
    return {item["student"]["full_name"]: item for item in response.json()["items"]}


@pytest.fixture
def seeded(catalog, debug):
    seed()


def test_the_first_student_has_no_request_and_can_ask(client, seeded):
    body = status_of(client, NO_REQUEST)

    assert body["request"] is None
    assert body["can_request"] is True
    assert AdvisingRequest.objects.filter(student__user=user_of(NO_REQUEST)).count() == 0


def test_the_second_student_has_a_pending_request_to_the_first_teacher(client, seeded):
    body = status_of(client, PENDING)

    assert body["request"]["status"] == "pending"
    assert body["request"]["teacher"]["id"] == user_of(TEACHER).id
    assert body["can_request"] is False
    assert body["blocked_reason"] == "pending_request"


def test_the_pending_request_is_close_to_the_seven_day_deadline(client, seeded):
    body = status_of(client, PENDING)

    expires_at = parse_datetime(body["request"]["expires_at"])
    left = expires_at - timezone.now()
    assert timedelta(hours=1) < left < timedelta(days=2)


def test_the_third_student_was_refused_and_can_ask_again(client, seeded):
    body = status_of(client, REFUSED)

    assert body["request"]["status"] == "rejected"
    assert body["request"]["teacher"]["id"] == user_of(TEACHER).id
    assert body["can_request"] is True
    assert body["blocked_reason"] is None


def test_the_fourth_student_has_an_unanswered_request_and_can_ask_again(client, seeded):
    body = status_of(client, UNANSWERED)

    assert body["request"]["status"] == "unanswered"
    assert body["can_request"] is True
    stored = AdvisingRequest.objects.get(student__user=user_of(UNANSWERED))
    assert stored.status == AdvisingRequestStatus.UNANSWERED
    assert timezone.now() - stored.requested_at > timedelta(days=7)


def test_the_first_teacher_lists_the_requests_of_different_students(client, seeded):
    received = received_by_teacher(client)

    statuses = {name: item["status"] for name, item in received.items()}
    assert statuses == {
        user_of(PENDING).full_name: "pending",
        user_of(REFUSED).full_name: "rejected",
        user_of(UNANSWERED).full_name: "unanswered",
        user_of(ACCEPTED[0]).full_name: "accepted",
        user_of(ACCEPTED[1]).full_name: "accepted",
        user_of(ACCEPTED[2]).full_name: "accepted",
    }


def test_the_other_course_student_has_no_request_yet(seeded):
    students = [user_of(slug) for slug in WITHOUT_REQUESTS]

    assert AdvisingRequest.objects.filter(student__user__in=students).count() == 0


def test_the_services_notify_the_teacher_and_the_students(seeded):
    teacher = user_of(TEACHER)
    received = Notification.objects.filter(
        recipient=teacher, type=NotificationType.ADVISING_REQUEST_RECEIVED
    )
    answered = Notification.objects.filter(
        recipient=user_of(REFUSED), type=NotificationType.ADVISING_REQUEST_ANSWERED
    )
    unanswered = Notification.objects.filter(
        recipient=user_of(UNANSWERED), type=NotificationType.ADVISING_REQUEST_UNANSWERED
    )

    # The three scenarios and the three accepted requests of the advisings.
    assert received.count() == 6
    assert answered.count() == 1
    assert answered.get().payload["decision"] == "rejected"
    assert unanswered.count() == 1


def test_running_it_again_duplicates_neither_requests_nor_notifications(seeded):
    requests_before = AdvisingRequest.objects.count()
    notifications_before = Notification.objects.count()

    seed()

    assert AdvisingRequest.objects.count() == requests_before == 6
    assert Notification.objects.count() == notifications_before
