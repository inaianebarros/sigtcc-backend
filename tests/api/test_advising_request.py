from datetime import timedelta
from threading import Barrier, Thread
from unittest.mock import patch

import pytest
from django.db import connection
from django.test import Client
from django.utils import timezone

from apps.advising.models import Advising, AdvisingRequest, AdvisingRequestStatus, AdvisingStatus
from apps.advising.services import (
    ACTIVE_ADVISING_MESSAGE as ACTIVE_ADVISING,
)
from apps.advising.services import (
    OTHER_COURSE_MESSAGE as OTHER_COURSE,
)
from apps.advising.services import (
    PENDING_REQUEST_MESSAGE as PENDING_REQUEST,
)
from apps.advising.services import (
    UNAVAILABLE_TEACHER_MESSAGE as UNAVAILABLE,
)
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
)

pytestmark = pytest.mark.django_db

REQUESTS_URL = "/api/orientacao/solicitacoes"


def make_pair():
    course = make_course()
    return make_student(course=course), make_teacher(courses=[course])


def post_request(client, user, teacher_id):
    return client.post(
        REQUESTS_URL,
        data={"teacher_id": teacher_id},
        content_type="application/json",
        headers=bearer(user),
    )


def test_a_student_requests_advising_from_an_available_teacher_of_their_course(client):
    student, teacher = make_pair()

    response = post_request(client, student.user, teacher.user_id)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    assert body["teacher"] == {"id": teacher.user_id, "full_name": teacher.user.full_name}
    advising_request = AdvisingRequest.objects.get(pk=body["id"])
    assert (advising_request.student, advising_request.teacher) == (student, teacher)
    assert advising_request.status == AdvisingRequestStatus.PENDING
    notification = Notification.objects.get()
    assert notification.recipient == teacher.user
    assert notification.type == NotificationType.ADVISING_REQUEST_RECEIVED
    assert (notification.reference_type, notification.reference_id) == (
        ADVISING_REQUEST_REFERENCE,
        advising_request.id,
    )


def make_unavailable(teacher):
    teacher.is_available = False
    teacher.save(update_fields=["is_available"])
    return teacher


def assert_conflict(response, message, requests_before=0):
    assert response.status_code == 409
    assert response.json() == {"detail": message}
    assert AdvisingRequest.objects.count() == requests_before
    assert not Notification.objects.exists()


def test_a_student_with_an_active_advising_cannot_request_another(client):
    student, teacher = make_pair()
    make_advising(student=student, teacher=make_teacher(courses=[student.course]))

    response = post_request(client, student.user, teacher.user_id)

    assert_conflict(response, ACTIVE_ADVISING)


def test_a_student_with_a_pending_request_cannot_send_another(client):
    student, teacher = make_pair()
    make_advising_request(student=student, teacher=make_teacher(courses=[student.course]))

    response = post_request(client, student.user, teacher.user_id)

    assert_conflict(response, PENDING_REQUEST, requests_before=1)


def test_a_teacher_of_another_course_cannot_be_requested(client):
    student = make_student()
    teacher = make_teacher(courses=[make_course()])

    response = post_request(client, student.user, teacher.user_id)

    assert_conflict(response, OTHER_COURSE)


def test_an_unavailable_teacher_cannot_be_requested(client):
    student, teacher = make_pair()
    make_unavailable(teacher)

    response = post_request(client, student.user, teacher.user_id)

    assert_conflict(response, UNAVAILABLE)


def test_the_first_violated_rule_wins_in_the_documented_order(client):
    course = make_course()
    unavailable_elsewhere = make_unavailable(make_teacher(courses=[make_course()]))

    active = make_student(course=course)
    make_advising(student=active, teacher=make_teacher(courses=[course]))
    pending = make_student(course=course)
    make_advising_request(student=pending, teacher=make_teacher(courses=[course]))
    free = make_student(course=course)

    results = {
        "active": post_request(client, active.user, unavailable_elsewhere.user_id),
        "pending": post_request(client, pending.user, unavailable_elsewhere.user_id),
        "course": post_request(client, free.user, unavailable_elsewhere.user_id),
    }
    both = make_unavailable(make_teacher(courses=[make_course()]))
    other_course_and_unavailable = post_request(client, free.user, both.user_id)

    assert results["active"].json() == {"detail": ACTIVE_ADVISING}
    assert results["pending"].json() == {"detail": PENDING_REQUEST}
    assert results["course"].json() == {"detail": OTHER_COURSE}
    assert other_course_and_unavailable.json() == {"detail": OTHER_COURSE}


def test_an_unknown_inactive_or_non_teacher_user_is_not_found(client):
    student, teacher = make_pair()
    teacher.user.is_active = False
    teacher.user.save(update_fields=["is_active"])
    other_student = make_student()

    for teacher_id in (999999, teacher.user_id, other_student.user_id):
        response = post_request(client, student.user, teacher_id)

        assert response.status_code == 404
        assert response.json() == {"detail": "Professor não encontrado."}
    assert not AdvisingRequest.objects.exists()


def test_only_students_can_request_and_visitors_are_not_authenticated(client):
    student, teacher = make_pair()

    as_teacher = post_request(client, teacher.user, teacher.user_id)
    anonymous = client.post(
        REQUESTS_URL, data={"teacher_id": teacher.user_id}, content_type="application/json"
    )

    assert as_teacher.status_code == 403
    assert anonymous.status_code == 401
    assert not AdvisingRequest.objects.exists()


def test_the_teacher_id_is_required(client):
    student, _ = make_pair()

    response = client.post(
        REQUESTS_URL, data={}, content_type="application/json", headers=bearer(student.user)
    )

    assert response.status_code == 422


def backdate(advising_request, **delta):
    AdvisingRequest.objects.filter(pk=advising_request.pk).update(
        requested_at=timezone.now() - timedelta(**delta)
    )


def test_a_pending_request_older_than_seven_days_is_released_and_replaced(client):
    student, teacher = make_pair()
    overdue = make_advising_request(student=student, teacher=make_teacher(courses=[student.course]))
    backdate(overdue, days=7, minutes=1)

    response = post_request(client, student.user, teacher.user_id)

    assert response.status_code == 201
    overdue.refresh_from_db()
    assert overdue.status == AdvisingRequestStatus.UNANSWERED
    assert AdvisingRequest.objects.filter(status=AdvisingRequestStatus.PENDING).count() == 1


def test_a_pending_request_younger_than_seven_days_still_blocks(client):
    student, teacher = make_pair()
    recent = make_advising_request(student=student, teacher=make_teacher(courses=[student.course]))
    backdate(recent, days=6, hours=23)

    response = post_request(client, student.user, teacher.user_id)

    assert_conflict(response, PENDING_REQUEST, requests_before=1)
    recent.refresh_from_db()
    assert recent.status == AdvisingRequestStatus.PENDING


@pytest.mark.parametrize(
    "previous", [AdvisingRequestStatus.REJECTED, AdvisingRequestStatus.UNANSWERED]
)
def test_a_student_can_ask_the_same_teacher_again_after_a_refusal_or_expiry(client, previous):
    student, teacher = make_pair()
    old = make_advising_request(student=student, teacher=teacher)
    AdvisingRequest.objects.filter(pk=old.pk).update(status=previous)

    response = post_request(client, student.user, teacher.user_id)

    assert response.status_code == 201
    assert AdvisingRequest.objects.filter(student=student, teacher=teacher).count() == 2


def test_a_student_whose_advising_ended_can_request_again(client):
    student, teacher = make_pair()
    ended = make_advising(student=student, teacher=make_teacher(courses=[student.course]))
    Advising.objects.filter(pk=ended.pk).update(
        status=AdvisingStatus.ENDED, ended_at=timezone.now(), end_reason="Encerrada no teste."
    )

    response = post_request(client, student.user, teacher.user_id)

    assert response.status_code == 201


def test_a_failing_notification_leaves_no_request_behind():
    student, teacher = make_pair()
    quiet_client = Client(raise_request_exception=False)

    with patch(
        "apps.advising.services.Notification.objects.create", side_effect=RuntimeError("boom")
    ):
        response = post_request(quiet_client, student.user, teacher.user_id)

    assert response.status_code == 500
    assert not AdvisingRequest.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_requests_from_the_same_student_leave_one_pending():
    student, teacher = make_pair()
    other = make_teacher(courses=[student.course])
    headers = bearer(student.user)
    start_together = Barrier(2)
    statuses, failures = [], []

    def send(teacher_id):
        try:
            start_together.wait(timeout=10)
            response = Client().post(
                REQUESTS_URL,
                data={"teacher_id": teacher_id},
                content_type="application/json",
                headers=headers,
            )
            statuses.append(response.status_code)
        except Exception as error:
            failures.append(error)
        finally:
            connection.close()

    threads = [Thread(target=send, args=(t.user_id,)) for t in (teacher, other)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not failures
    assert sorted(statuses) == [201, 409]
    assert AdvisingRequest.objects.filter(status=AdvisingRequestStatus.PENDING).count() == 1


def test_the_openapi_documents_the_endpoint_and_its_conflicts(client):
    schema = client.get("/api/openapi.json").json()

    responses = schema["paths"]["/api/orientacao/solicitacoes"]["post"]["responses"]
    assert {"201", "404", "409"} <= set(responses)


@pytest.mark.parametrize("make_blocker", [make_unavailable, lambda teacher: teacher])
def test_an_overdue_request_stays_pending_when_a_later_rule_rejects_the_new_one(
    client, make_blocker
):
    student = make_student()
    overdue = make_advising_request(student=student, teacher=make_teacher(courses=[student.course]))
    backdate(overdue, days=8)
    blocked = make_blocker(make_teacher(courses=[make_course()]))

    response = post_request(client, student.user, blocked.user_id)

    assert response.status_code == 409
    overdue.refresh_from_db()
    assert overdue.status == AdvisingRequestStatus.PENDING
    assert AdvisingRequest.objects.count() == 1


@pytest.mark.parametrize("teacher_id", [-1, 0, 2**31, 2**63, 10**30])
def test_an_out_of_range_teacher_id_is_not_a_server_error(client, teacher_id):
    student, _ = make_pair()

    response = post_request(client, student.user, teacher_id)

    assert response.status_code in (404, 422)


def test_an_inactive_student_cannot_request(client):
    student, teacher = make_pair()
    headers = bearer(student.user)
    student.user.is_active = False
    student.user.save(update_fields=["is_active"])

    response = client.post(
        REQUESTS_URL,
        data={"teacher_id": teacher.user_id},
        content_type="application/json",
        headers=headers,
    )

    assert response.status_code == 401
    assert not AdvisingRequest.objects.exists()
