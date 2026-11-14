import datetime

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import UserType
from apps.activities.models import Activity, ActivityStatus
from apps.advising.models import Advising, AdvisingStatus
from tests.api.helpers import bearer
from tests.factories import make_advising, make_course, make_student, make_teacher, make_user

pytestmark = pytest.mark.django_db

URL = "/api/atividades/resumo"


def summary(client, user, **params):
    return client.get(URL, data=params, headers=bearer(user))


def add_activity(advising, status=ActivityStatus.PENDING, **extra):
    return Activity.objects.create(
        advising=advising,
        title="Atividade",
        description="Descrição da atividade",
        deadline=timezone.localdate() + datetime.timedelta(days=7),
        status=status,
        **extra,
    )


def advised_pair(teacher=None, course=None):
    course = course or make_course()
    teacher = teacher or make_teacher(courses=[course])
    student = make_student(course=course)
    return student, teacher, make_advising(student=student, teacher=teacher)


def fill(advising, pending=0, in_progress=0, in_review=0, completed=0):
    for status, amount in (
        (ActivityStatus.PENDING, pending),
        (ActivityStatus.IN_PROGRESS, in_progress),
        (ActivityStatus.IN_REVIEW, in_review),
        (ActivityStatus.COMPLETED, completed),
    ):
        for _ in range(amount):
            add_activity(advising, status)


def test_the_student_reads_the_total_the_pending_and_the_completed_of_their_activities(client):
    student, _, advising = advised_pair()
    fill(advising, pending=2, in_progress=1, in_review=1, completed=3)

    response = summary(client, student.user)

    assert response.status_code == 200
    assert response.json() == {"total": 7, "pending": 2, "completed": 3}


def test_the_teacher_reads_the_numbers_of_all_their_active_advisees(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    _, _, first = advised_pair(teacher, course)
    _, _, second = advised_pair(teacher, course)
    fill(first, pending=1, completed=2)
    fill(second, pending=2, in_progress=1, completed=1)

    response = summary(client, teacher.user)

    assert response.json() == {"total": 7, "pending": 3, "completed": 3}


def test_in_progress_and_in_review_count_only_in_the_total(client):
    student, _, advising = advised_pair()
    fill(advising, in_progress=2, in_review=3)

    assert summary(client, student.user).json() == {"total": 5, "pending": 0, "completed": 0}


def test_deleted_archived_and_ended_advising_activities_never_count(client):
    student, teacher, advising = advised_pair()
    fill(advising, pending=1, completed=1)
    add_activity(advising, ActivityStatus.ARCHIVED)
    add_activity(advising, ActivityStatus.PENDING, deleted_at=timezone.now())
    add_activity(advising, ActivityStatus.COMPLETED, deleted_at=timezone.now())
    _, _, other = advised_pair(teacher, student.course)
    fill(other, pending=1)
    Advising.objects.filter(pk=other.pk).update(
        status=AdvisingStatus.ENDED, ended_at=timezone.now(), end_reason="Encerrada no teste."
    )

    assert summary(client, student.user).json() == {"total": 2, "pending": 1, "completed": 1}
    assert summary(client, teacher.user).json() == {"total": 2, "pending": 1, "completed": 1}


def test_nothing_of_other_users_counts(client):
    student, _, advising = advised_pair()
    _, other_teacher, other_advising = advised_pair()
    fill(advising, pending=1)
    fill(other_advising, pending=4, completed=4)

    assert summary(client, student.user).json() == {"total": 1, "pending": 1, "completed": 0}
    assert summary(client, other_teacher.user).json() == {"total": 8, "pending": 4, "completed": 4}


def test_without_activities_the_numbers_are_zero(client):
    student, teacher, _ = advised_pair()
    lonely = make_student()

    zeros = {"total": 0, "pending": 0, "completed": 0}
    assert summary(client, student.user).json() == zeros
    assert summary(client, teacher.user).json() == zeros
    assert summary(client, lonely.user).json() == zeros


def test_no_filter_changes_the_numbers(client):
    student, _, advising = advised_pair()
    fill(advising, pending=2, completed=1)

    plain = summary(client, student.user).json()
    filtered = summary(
        client, student.user, status="completed", search="x", student_id=1, overdue="true"
    ).json()

    assert filtered == plain == {"total": 3, "pending": 2, "completed": 1}


def test_the_numbers_are_the_ones_of_the_dashboards(client):
    student, teacher, advising = advised_pair()
    fill(advising, pending=2, in_progress=1, in_review=1, completed=2)

    student_dashboard = client.get("/api/painel/aluno", headers=bearer(student.user)).json()
    teacher_dashboard = client.get("/api/painel/professor", headers=bearer(teacher.user)).json()

    for dashboard, user in ((student_dashboard, student.user), (teacher_dashboard, teacher.user)):
        indicators = dashboard["activities"]
        assert summary(client, user).json() == {
            "total": indicators["total"],
            "pending": indicators["pending"],
            "completed": indicators["completed"],
        }


def test_admins_are_refused_and_visitors_are_not_authenticated(client):
    assert summary(client, make_user(UserType.ADMIN)).status_code == 403
    assert client.get(URL).status_code == 401


def test_the_number_of_queries_does_not_grow_with_the_number_of_activities(client):
    student, _, advising = advised_pair()
    add_activity(advising)

    def count():
        with CaptureQueriesContext(connection) as queries:
            assert summary(client, student.user).status_code == 200
        return len(queries)

    few = count()
    fill(advising, pending=10, in_progress=5, completed=10)

    assert count() == few


def test_the_openapi_documents_the_reading_and_its_fields(client):
    schema = client.get("/api/openapi.json").json()

    operation = schema["paths"]["/api/atividades/resumo"]["get"]
    assert {"200", "401", "403"} <= set(operation["responses"])
    assert operation["summary"] and "RN35" in operation["description"]
    assert {"total", "pending", "completed"} <= set(
        schema["components"]["schemas"]["ActivitySummaryOut"]["properties"]
    )
