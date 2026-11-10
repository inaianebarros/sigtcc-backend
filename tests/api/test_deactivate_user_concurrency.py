"""A request for orientation racing with the deactivation of the teacher (RN19, ADR 0004).

The request has to wait for the deactivation and then be refused: no pending request may be left
for a deactivated teacher. Real threads and transactions against PostgreSQL.
"""

import pytest

from apps.accounts.deactivation import deactivate_user
from apps.accounts.models import User
from apps.advising.models import AdvisingRequest
from tests.api.helpers import bearer, post_json, send_while_a_lock_is_held
from tests.factories import make_course, make_student, make_teacher

pytestmark = pytest.mark.django_db(transaction=True)


def test_a_request_waiting_for_the_deactivation_is_refused_and_leaves_nothing(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)

    def deactivate_the_teacher(wait_for_the_request):
        locked = User.objects.select_for_update().get(pk=teacher.user_id)
        wait_for_the_request()
        deactivate_user(locked)

    response = send_while_a_lock_is_held(
        deactivate_the_teacher,
        lambda: post_json(
            client,
            "/api/orientacao/solicitacoes",
            {"teacher_id": teacher.user_id},
            headers=bearer(student.user),
        ),
    )

    assert response.status_code == 404
    assert not AdvisingRequest.objects.filter(teacher=teacher).exists()
    assert not User.objects.get(pk=teacher.user_id).is_active
