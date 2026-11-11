"""The topic, the meetings and the notifications of the active advising of student 5 (#143)."""

from datetime import datetime
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.accounts.demo_seed import DEMO_DOMAIN
from apps.accounts.models import User
from apps.advising.models import Advising, ThesisTopic
from apps.meetings.models import Meeting, MeetingStatus
from apps.notifications.models import Notification, NotificationType
from tests.api.helpers import bearer

pytestmark = pytest.mark.django_db

MEETINGS_URL = "/api/reunioes"
NOTIFICATIONS_URL = "/api/notificacoes"
UNREAD_URL = "/api/notificacoes/nao-lidas/contagem"
STUDENT = "aluno5.orientacao-completa"
EMPTY_STUDENT = "aluno6.orientacao-vazia"
TEACHER = "prof1.orientandos"
SITUATIONS = {"pending", "accepted", "rejected", "cancelled", "held", "expired"}


def user_of(slug):
    return User.objects.get(email=f"{slug}@{DEMO_DOMAIN}")


def seed():
    out = StringIO()
    call_command("seed_demo_data", stdout=out)
    return out.getvalue()


@pytest.fixture
def seeded(catalog, debug):
    seed()


def meetings_of(client, slug, **params):
    response = client.get(MEETINGS_URL, params, headers=bearer(user_of(slug)))
    return response.json()["items"]


def advising():
    return Advising.objects.get(student__user=user_of(STUDENT))


def test_the_complete_student_has_the_thesis_topic_of_the_active_advising(seeded):
    topic = ThesisTopic.objects.get(advising=advising())

    assert len(topic.description) >= 10
    assert not ThesisTopic.objects.filter(advising__student__user=user_of(EMPTY_STUDENT)).exists()


def test_the_meetings_cover_every_situation(client, seeded):
    items = meetings_of(client, STUDENT)

    assert {item["situation"] for item in items} == SITUATIONS


def test_there_is_a_pending_meeting_of_each_author(client, seeded):
    pending = meetings_of(client, STUDENT, situation="pending")

    assert len(pending) == 2
    assert {item["author"]["id"] for item in pending} == {
        user_of(STUDENT).id,
        user_of(TEACHER).id,
    }


def test_each_side_can_answer_the_pending_meeting_of_the_other(client, seeded):
    student_view = {m["author"]["id"]: m for m in meetings_of(client, STUDENT, situation="pending")}
    teacher_view = {m["author"]["id"]: m for m in meetings_of(client, TEACHER, situation="pending")}

    assert student_view[user_of(TEACHER).id]["can_answer"] is True
    assert student_view[user_of(STUDENT).id]["can_answer"] is False
    assert teacher_view[user_of(STUDENT).id]["can_answer"] is True
    assert teacher_view[user_of(TEACHER).id]["can_answer"] is False


def test_the_rejected_meeting_keeps_the_justification(seeded):
    rejected = Meeting.objects.get(advising=advising(), status=MeetingStatus.REJECTED)

    assert len(rejected.rejection_reason) >= 10


def test_two_meetings_were_cancelled_by_the_parties_with_a_reason(seeded):
    cancelled = Meeting.objects.filter(advising=advising(), status=MeetingStatus.CANCELLED)

    assert cancelled.count() == 2
    for meeting in cancelled:
        assert meeting.cancelled_by_system is False
        assert len(meeting.cancellation_reason) >= 10


def test_there_is_a_held_meeting_in_the_past_and_an_expired_one(client, seeded):
    held = meetings_of(client, STUDENT, situation="held")
    expired = meetings_of(client, STUDENT, situation="expired")

    assert len(held) == 1
    assert len(expired) == 1
    assert datetime.fromisoformat(held[0]["scheduled_at"]) < timezone.now()
    assert datetime.fromisoformat(expired[0]["scheduled_at"]) < timezone.now()


def test_the_teacher_lists_the_meetings_of_the_advisee(client, seeded):
    items = meetings_of(client, TEACHER, student_id=user_of(STUDENT).id)

    assert {item["situation"] for item in items} == SITUATIONS


def test_the_dashboards_show_only_the_accepted_future_meeting(client, seeded):
    student = client.get("/api/painel/aluno", headers=bearer(user_of(STUDENT))).json()
    teacher = client.get("/api/painel/professor", headers=bearer(user_of(TEACHER))).json()

    assert len(student["next_meetings"]["items"]) == 1
    assert len(teacher["next_meetings"]["items"]) == 1


def test_the_student_has_read_and_unread_notifications_and_the_bell_counts_the_unread(
    client, seeded
):
    headers = bearer(user_of(STUDENT))

    read = client.get(NOTIFICATIONS_URL, {"is_read": "true"}, headers=headers).json()
    unread = client.get(NOTIFICATIONS_URL, {"is_read": "false"}, headers=headers).json()
    count = client.get(UNREAD_URL, headers=headers).json()["count"]

    assert read["count"] > 0
    assert unread["count"] > 0
    assert count == unread["count"]


def test_the_teacher_also_has_read_and_unread_notifications(client, seeded):
    headers = bearer(user_of(TEACHER))

    read = client.get(NOTIFICATIONS_URL, {"is_read": "true"}, headers=headers).json()
    unread = client.get(NOTIFICATIONS_URL, {"is_read": "false"}, headers=headers).json()

    assert read["count"] > 0
    assert unread["count"] > 0


def test_the_meeting_events_are_among_the_unread_ones(seeded):
    unread_types = set(
        Notification.objects.filter(recipient=user_of(STUDENT), is_read=False).values_list(
            "type", flat=True
        )
    )

    assert NotificationType.MEETING_REQUESTED in unread_types
    assert NotificationType.MEETING_ANSWERED in unread_types


def test_the_notifications_come_from_the_services(seeded):
    requested = Notification.objects.filter(
        recipient=user_of(TEACHER), type=NotificationType.MEETING_REQUESTED
    )
    answered = Notification.objects.filter(
        recipient=user_of(TEACHER), type=NotificationType.MEETING_ANSWERED
    )

    assert requested.count() >= 3
    assert answered.count() >= 3


def test_running_it_again_duplicates_nothing_and_keeps_the_read_state(seeded):
    before = (
        Meeting.objects.count(),
        ThesisTopic.objects.count(),
        Notification.objects.count(),
        Notification.objects.filter(is_read=True).count(),
    )

    seed()

    assert before == (
        Meeting.objects.count(),
        ThesisTopic.objects.count(),
        Notification.objects.count(),
        Notification.objects.filter(is_read=True).count(),
    )


def test_the_meetings_cancelled_by_the_system_of_the_ended_advising_are_kept(seeded):
    system = Meeting.objects.filter(status=MeetingStatus.CANCELLED, cancelled_by_system=True)

    assert system.count() == 2


def test_running_it_again_does_not_overwrite_a_topic_edited_by_hand(seeded):
    ThesisTopic.objects.filter(advising=advising()).update(description="Tema editado à mão")

    seed()

    assert ThesisTopic.objects.get(advising=advising()).description == "Tema editado à mão"
