"""The report of an ended advising: the header and the totals (RF61, RN47, RN33, RN12)."""

from datetime import date, timedelta

import pytest
from django.utils import timezone

from apps.accounts.deactivation import deactivate_user
from apps.activities.models import Activity, ActivityStatus
from apps.advising.models import Advising, AdvisingStatus, ThesisTopic
from apps.advising.services import SYSTEM_END_REASON
from apps.meetings.models import Meeting, MeetingStatus
from tests.api.helpers import bearer
from tests.factories import make_admin, make_advising, make_student, make_teacher

pytestmark = pytest.mark.django_db

REASON = "Concluímos o ciclo de orientação do TCC."
FIELDS = {
    "id",
    "counterpart",
    "started_at",
    "ended_at",
    "reason",
    "thesis_topic",
    "records_count",
    "meetings_count",
    "activities_count",
}


def report_url(advising_id):
    return f"/api/orientacao/historico/{advising_id}/relatorio"


def report(client, user, advising_id):
    return client.get(report_url(advising_id), headers=bearer(user))


def ended_advising(student=None, teacher=None, topic=None, reason=REASON):
    advising = make_advising(student=student, teacher=teacher)
    if topic is not None:
        ThesisTopic.objects.create(advising=advising, description=topic)
    Advising.objects.filter(pk=advising.pk).update(
        status=AdvisingStatus.ENDED, ended_at=timezone.now(), end_reason=reason
    )
    advising.refresh_from_db()
    return advising


def add_meeting(advising, status=MeetingStatus.ACCEPTED, days_from_now=-3, held=False):
    start = timezone.now() + timedelta(days=days_from_now)
    return Meeting.objects.create(
        advising=advising,
        author=advising.teacher.user,
        scheduled_at=start,
        ends_at=start + timedelta(hours=1),
        description="Reunião de acompanhamento",
        status=status,
        rejection_reason="Sem horário." if status == MeetingStatus.REJECTED else "",
        cancellation_reason="Imprevisto." if status == MeetingStatus.CANCELLED else "",
        held_at=timezone.now() if held else None,
        held_by=advising.teacher.user if held else None,
    )


def add_activity(advising, status=ActivityStatus.COMPLETED, deleted=False):
    return Activity.objects.create(
        advising=advising,
        title="Revisar capítulo",
        description="Entregar o capítulo",
        deadline=date(2026, 11, 20),
        status=status,
        deleted_at=timezone.now() if deleted else None,
    )


def test_a_student_receives_the_header_and_the_totals_of_the_ended_advising(client):
    teacher = make_teacher(full_name="Paulo Professor")
    student = make_student()
    advising = ended_advising(student, teacher, topic="Aprendizado de máquina")
    add_meeting(advising)
    add_activity(advising)
    add_activity(advising, status=ActivityStatus.ARCHIVED)

    response = report(client, student.user, advising.id)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == FIELDS
    assert body["id"] == advising.id
    assert body["counterpart"] == {"id": teacher.user_id, "full_name": "Paulo Professor"}
    assert body["reason"] == REASON
    assert body["thesis_topic"] == "Aprendizado de máquina"
    assert body["started_at"] and body["ended_at"]
    assert (body["records_count"], body["meetings_count"], body["activities_count"]) == (3, 1, 2)


def test_a_teacher_receives_the_report_with_the_student_as_the_other_party(client):
    teacher = make_teacher()
    student = make_student(full_name="Ana Aluna")
    advising = ended_advising(student, teacher)

    body = report(client, teacher.user, advising.id).json()

    assert body["counterpart"] == {"id": student.user_id, "full_name": "Ana Aluna"}


def test_the_topic_is_null_when_the_advising_never_had_one(client):
    student = make_student()
    advising = ended_advising(student)

    assert report(client, student.user, advising.id).json()["thesis_topic"] is None


def test_only_the_held_meetings_count(client):
    student = make_student()
    advising = ended_advising(student)
    add_meeting(advising, days_from_now=-3)  # accepted and past: held
    add_meeting(advising, days_from_now=2, held=True)  # accepted, marked as held before its time
    add_meeting(advising, status=MeetingStatus.PENDING)
    add_meeting(advising, status=MeetingStatus.REJECTED)
    add_meeting(advising, status=MeetingStatus.CANCELLED)
    add_meeting(advising, days_from_now=2)  # accepted, future, not marked: did not happen

    body = report(client, student.user, advising.id).json()

    assert body["meetings_count"] == 2


def test_only_the_completed_and_the_archived_activities_that_were_not_deleted_count(client):
    student = make_student()
    advising = ended_advising(student)
    add_activity(advising, status=ActivityStatus.COMPLETED)
    add_activity(advising, status=ActivityStatus.ARCHIVED)
    add_activity(advising, status=ActivityStatus.COMPLETED, deleted=True)
    add_activity(advising, status=ActivityStatus.ARCHIVED, deleted=True)
    add_activity(advising, status=ActivityStatus.PENDING)
    add_activity(advising, status=ActivityStatus.IN_PROGRESS)
    add_activity(advising, status=ActivityStatus.IN_REVIEW)

    body = report(client, student.user, advising.id).json()

    assert body["activities_count"] == 2


def test_the_records_total_is_the_sum_and_counts_only_the_records_of_this_advising(client):
    student, teacher = make_student(), make_teacher()
    first = ended_advising(student, teacher)
    other = ended_advising(make_student(), teacher)
    add_meeting(first)
    add_meeting(first)
    add_activity(first)
    add_meeting(other)
    add_activity(other)
    add_activity(other)

    body = report(client, student.user, first.id).json()

    assert (body["meetings_count"], body["activities_count"]) == (2, 1)
    assert body["records_count"] == body["meetings_count"] + body["activities_count"] == 3


def test_an_ended_advising_without_records_has_zero_totals(client):
    student = make_student()
    advising = ended_advising(student)

    body = report(client, student.user, advising.id).json()

    assert (body["records_count"], body["meetings_count"], body["activities_count"]) == (0, 0, 0)


def test_the_report_of_another_students_advising_is_a_404_like_an_unknown_id(client):
    advising = ended_advising()
    stranger = make_student()

    other = report(client, stranger.user, advising.id)
    unknown = report(client, stranger.user, advising.id + 1000)

    assert other.status_code == unknown.status_code == 404
    assert other.json() == unknown.json()


def test_the_report_of_another_teachers_advising_is_a_404(client):
    advising = ended_advising()

    assert report(client, make_teacher().user, advising.id).status_code == 404


def test_the_report_of_an_active_advising_is_a_404_for_both_parties(client):
    student, teacher = make_student(), make_teacher()
    advising = make_advising(student=student, teacher=teacher)

    assert report(client, student.user, advising.id).status_code == 404
    assert report(client, teacher.user, advising.id).status_code == 404


def test_a_visitor_is_unauthorized_and_the_administrator_is_forbidden(client):
    advising = ended_advising()

    assert client.get(report_url(advising.id)).status_code == 401
    assert report(client, make_admin(), advising.id).status_code == 403


@pytest.mark.parametrize("deactivated", ["student", "teacher"])
def test_an_advising_ended_by_the_system_has_a_report_for_the_other_party(client, deactivated):
    student, teacher = make_student(), make_teacher()
    advising = make_advising(student=student, teacher=teacher)
    deactivate_user(student.user if deactivated == "student" else teacher.user)
    remaining = teacher.user if deactivated == "student" else student.user

    response = report(client, remaining, advising.id)

    assert response.status_code == 200
    assert response.json()["reason"] == SYSTEM_END_REASON


def test_the_openapi_documents_the_report(client):
    schema = client.get("/api/openapi.json").json()

    operation = schema["paths"]["/api/orientacao/historico/{advising_id}/relatorio"]["get"]
    assert operation["description"]
    assert {"401", "403", "404"} <= set(operation["responses"])
    fields = schema["components"]["schemas"]["AdvisingReportOut"]["properties"]
    assert set(fields) == FIELDS
