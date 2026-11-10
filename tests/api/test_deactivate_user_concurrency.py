"""A request for orientation racing with the deactivation of the teacher (RN19, ADR 0004).

The request has to wait for the deactivation and then be refused: no pending request may be left
for a deactivated teacher. Real threads and transactions against PostgreSQL.
"""

import threading

import pytest
from django.db import connection, transaction

from apps.accounts.deactivation import deactivate_user
from apps.accounts.models import User
from apps.advising.models import AdvisingRequest
from tests.api.helpers import bearer, post_json, wait_for_a_request_blocked_on_a_lock
from tests.factories import make_course, make_student, make_teacher

pytestmark = pytest.mark.django_db(transaction=True)


def test_a_request_waiting_for_the_deactivation_is_refused_and_leaves_nothing(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    holding_the_lock = threading.Event()
    failures = []

    def deactivate_while_holding_the_lock():
        try:
            with transaction.atomic():
                locked = User.objects.select_for_update().get(pk=teacher.user_id)
                holding_the_lock.set()
                wait_for_a_request_blocked_on_a_lock(timeout=20)
                deactivate_user(locked)
        except Exception as error:  # noqa: BLE001 - reported to the main thread
            failures.append(error)
            holding_the_lock.set()
        finally:
            connection.close()

    deactivating = threading.Thread(target=deactivate_while_holding_the_lock)
    deactivating.start()
    assert holding_the_lock.wait(timeout=10)
    response = post_json(
        client,
        "/api/orientacao/solicitacoes",
        {"teacher_id": teacher.user_id},
        headers=bearer(student.user),
    )
    deactivating.join(timeout=20)

    assert failures == []
    assert response.status_code == 404
    assert not AdvisingRequest.objects.filter(teacher=teacher).exists()
    assert not User.objects.get(pk=teacher.user_id).is_active
