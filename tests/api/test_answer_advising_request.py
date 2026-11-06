from datetime import timedelta
from threading import Barrier, Thread
from unittest.mock import patch

import pytest
from django.db import connection
from django.test import Client
from django.utils import timezone

from apps.accounts.models import UserType
from apps.advising.models import Advising, AdvisingRequest, AdvisingRequestStatus, AdvisingStatus
from apps.notifications.models import (
    ADVISING_REQUEST_REFERENCE,
    Notification,
    NotificationType,
)
from tests.api.helpers import bearer
from tests.factories import (
    make_advising,
    make_advising_request,
    make_course,
    make_student,
    make_teacher,
    make_user,
)

pytestmark = pytest.mark.django_db


def answer_url(request_id):
    return f"/api/orientacao/solicitacoes/{request_id}/responder"


def make_received():
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    return make_advising_request(student=student, teacher=teacher)


def answer(client, user, request_id, action):
    return client.post(
        answer_url(request_id),
        data={"action": action},
        content_type="application/json",
        headers=bearer(user),
    )


def test_a_teacher_accepts_a_pending_request_and_an_active_advising_is_created(client):
    received = make_received()

    response = answer(client, received.teacher.user, received.id, "accept")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == received.id
    assert body["status"] == "accepted"
    assert body["responded_at"] is not None
    assert body["expires_at"] is None
    assert body["student"]["id"] == received.student.user_id
    received.refresh_from_db()
    assert received.status == AdvisingRequestStatus.ACCEPTED
    assert received.responded_at is not None
    advising = Advising.objects.get()
    assert (advising.student, advising.teacher, advising.request) == (
        received.student,
        received.teacher,
        received,
    )
    assert advising.status == AdvisingStatus.ACTIVE
    assert body["advising"] == {"id": advising.id, "started_at": body["advising"]["started_at"]}
    notification = Notification.objects.get()
    assert notification.recipient == received.student.user
    assert notification.type == NotificationType.ADVISING_REQUEST_ANSWERED
    assert (notification.reference_type, notification.reference_id) == (
        ADVISING_REQUEST_REFERENCE,
        received.id,
    )


def test_a_teacher_rejects_a_pending_request_without_creating_an_advising(client):
    received = make_received()

    response = answer(client, received.teacher.user, received.id, "reject")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    assert body["responded_at"] is not None
    assert body["advising"] is None
    received.refresh_from_db()
    assert received.status == AdvisingRequestStatus.REJECTED
    assert not Advising.objects.exists()
    notification = Notification.objects.get()
    assert notification.recipient == received.student.user
    assert notification.type == NotificationType.ADVISING_REQUEST_ANSWERED


def assert_untouched(received, before_status, message, response):
    assert response.status_code == 409
    assert response.json() == {"detail": message}
    received.refresh_from_db()
    assert received.status == before_status
    assert received.responded_at is None or before_status != AdvisingRequestStatus.PENDING
    assert not Advising.objects.exists()
    assert not Notification.objects.exists()


@pytest.mark.parametrize("first", [AdvisingRequestStatus.ACCEPTED, AdvisingRequestStatus.REJECTED])
@pytest.mark.parametrize("action", ["accept", "reject"])
def test_an_answered_request_cannot_be_answered_again(client, first, action):
    received = make_received()
    AdvisingRequest.objects.filter(pk=received.pk).update(status=first, responded_at=timezone.now())

    response = answer(client, received.teacher.user, received.id, action)

    assert_untouched(received, first, "Esta solicitação já foi respondida.", response)


@pytest.mark.parametrize("action", ["accept", "reject"])
@pytest.mark.parametrize(
    ("status", "days_ago"),
    [(AdvisingRequestStatus.PENDING, 8), (AdvisingRequestStatus.UNANSWERED, 9)],
)
def test_an_overdue_request_cannot_be_answered(client, action, status, days_ago):
    received = make_received()
    AdvisingRequest.objects.filter(pk=received.pk).update(
        status=status, requested_at=timezone.now() - timedelta(days=days_ago)
    )

    response = answer(client, received.teacher.user, received.id, action)

    assert_untouched(
        received, status, "O prazo para responder esta solicitação terminou.", response
    )


@pytest.mark.parametrize("action", ["accept", "reject"])
def test_a_request_from_an_inactive_student_cannot_be_answered(client, action):
    received = make_received()
    received.student.user.is_active = False
    received.student.user.save(update_fields=["is_active"])

    response = answer(client, received.teacher.user, received.id, action)

    assert_untouched(
        received, AdvisingRequestStatus.PENDING, "Este aluno não está mais ativo.", response
    )


def test_accepting_is_refused_if_the_student_already_has_an_active_advising(client):
    received = make_received()
    make_advising(student=received.student, teacher=make_teacher(courses=[received.student.course]))

    response = answer(client, received.teacher.user, received.id, "accept")

    assert response.status_code == 409
    assert response.json() == {"detail": "O aluno já tem um orientador ativo."}
    received.refresh_from_db()
    assert received.status == AdvisingRequestStatus.PENDING
    assert Advising.objects.count() == 1
    assert not Notification.objects.exists()


def test_an_unavailable_teacher_or_one_without_the_students_course_can_still_answer(client):
    received = make_received()
    teacher = received.teacher
    teacher.is_available = False
    teacher.save(update_fields=["is_available"])
    teacher.courses.clear()

    response = answer(client, teacher.user, received.id, "accept")

    assert response.status_code == 200
    assert Advising.objects.filter(student=received.student, teacher=teacher).exists()


def test_another_teachers_or_an_unknown_request_is_not_found(client):
    received = make_received()
    outsider = make_teacher()

    for request_id in (received.id, 999999):
        user = outsider.user
        response = answer(client, user, request_id, "accept")

        assert response.status_code == 404
        assert response.json() == {"detail": "Solicitação não encontrada."}
    received.refresh_from_db()
    assert received.status == AdvisingRequestStatus.PENDING


def test_only_teachers_can_answer_and_visitors_are_not_authenticated(client):
    received = make_received()

    as_student = answer(client, received.student.user, received.id, "accept")
    as_admin = answer(client, make_user(UserType.ADMIN), received.id, "accept")
    anonymous = client.post(
        answer_url(received.id), data={"action": "accept"}, content_type="application/json"
    )

    assert (as_student.status_code, as_admin.status_code) == (403, 403)
    assert anonymous.status_code == 401
    received.refresh_from_db()
    assert received.status == AdvisingRequestStatus.PENDING


@pytest.mark.parametrize("payload", [{}, {"action": "cancel"}, {"action": ""}])
def test_the_action_is_required_and_must_be_known(client, payload):
    received = make_received()

    response = client.post(
        answer_url(received.id),
        data=payload,
        content_type="application/json",
        headers=bearer(received.teacher.user),
    )

    assert response.status_code == 422


def test_a_failing_notification_leaves_the_request_pending_and_creates_no_advising():
    received = make_received()
    quiet_client = Client(raise_request_exception=False)

    with patch(
        "apps.advising.services.Notification.objects.create", side_effect=RuntimeError("boom")
    ):
        response = answer(quiet_client, received.teacher.user, received.id, "accept")

    assert response.status_code == 500
    received.refresh_from_db()
    assert received.status == AdvisingRequestStatus.PENDING
    assert received.responded_at is None
    assert not Advising.objects.exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("actions", [("accept", "accept"), ("accept", "reject")])
def test_two_simultaneous_answers_leave_exactly_one_winner(actions):
    received = make_received()
    headers = bearer(received.teacher.user)
    start_together = Barrier(2)
    statuses, failures = [], []

    def send(action):
        try:
            start_together.wait(timeout=10)
            response = Client().post(
                answer_url(received.id),
                data={"action": action},
                content_type="application/json",
                headers=headers,
            )
            statuses.append(response.status_code)
        except Exception as error:
            failures.append(error)
        finally:
            connection.close()

    threads = [Thread(target=send, args=(action,)) for action in actions]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not failures
    assert sorted(statuses) == [200, 409]
    assert Notification.objects.count() == 1
    assert Advising.objects.count() == (1 if received_status(received) == "accepted" else 0)


def received_status(received):
    received.refresh_from_db()
    return received.status


def test_the_openapi_documents_the_answer_endpoint(client):
    schema = client.get("/api/openapi.json").json()

    operation = schema["paths"]["/api/orientacao/solicitacoes/{request_id}/responder"]["post"]
    assert {"200", "404", "409"} <= set(operation["responses"])
