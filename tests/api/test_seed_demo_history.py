"""The ended advising and the course isolation of the demonstration data (#141)."""

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.accounts.demo_seed import DEMO_DOMAIN
from apps.accounts.models import User
from apps.activities.models import Activity, ActivityStatus
from apps.advising.models import Advising, AdvisingStatus, ThesisTopic
from apps.meetings.models import SYSTEM_CANCELLATION_REASON, Meeting, MeetingStatus
from apps.notifications.models import Notification, NotificationType
from tests.api.helpers import bearer, post_json

pytestmark = pytest.mark.django_db

ENDED_STUDENT = "aluno7.orientacao-encerrada"
OTHER_COURSE_STUDENT = "aluno8.outro-curso"
OTHER_COURSE_TEACHER = "prof4.outro-curso"
TEACHER = "prof1.orientandos"
EMPTY_TEACHER = "prof2.sem-orientandos"
COMPLETE_STUDENT = "aluno5.orientacao-completa"
FIRST_STUDENT = "aluno1.sem-orientacao"
EMPTY = {"items": [], "count": 0}


def user_of(slug):
    return User.objects.get(email=f"{slug}@{DEMO_DOMAIN}")


def seed():
    call_command("seed_demo_data", stdout=StringIO())


@pytest.fixture
def seeded(catalog, debug):
    seed()


def get(client, slug, url, **params):
    return client.get(url, params, headers=bearer(user_of(slug)))


def test_the_seventh_student_has_an_ended_advising_with_the_first_teacher(seeded):
    advising = Advising.objects.get(student__user=user_of(ENDED_STUDENT))

    assert advising.status == AdvisingStatus.ENDED
    assert advising.teacher.user == user_of(TEACHER)
    assert 10 <= len(advising.end_reason) <= 500
    assert timezone.now() - advising.ended_at > timedelta(days=7)
    assert advising.started_at < advising.ended_at


def test_the_history_of_the_student_shows_the_teacher_the_dates_the_reason_and_the_frozen_topic(
    client, seeded
):
    response = get(client, ENDED_STUDENT, "/api/orientacao/historico")

    body = response.json()
    assert body["count"] == 1
    item = body["items"][0]
    advising = Advising.objects.get(student__user=user_of(ENDED_STUDENT))
    assert item["counterpart"]["id"] == user_of(TEACHER).id
    assert item["reason"] == advising.end_reason
    assert item["thesis_topic"] == ThesisTopic.objects.get(advising=advising).description
    assert parse_datetime(item["started_at"]) < parse_datetime(item["ended_at"])


def test_the_teacher_sees_the_ended_advising_in_the_own_history(client, seeded):
    response = get(client, TEACHER, "/api/orientacao/historico")

    items = response.json()["items"]
    assert [item["counterpart"]["id"] for item in items] == [user_of(ENDED_STUDENT).id]


def test_the_seventh_student_is_free_to_ask_again(client, seeded):
    body = get(client, ENDED_STUDENT, "/api/orientacao/minha-solicitacao").json()

    assert body["advising"] is None
    assert body["can_request"] is True
    assert body["blocked_reason"] is None


def test_the_ending_archived_the_open_activities(seeded):
    advising = Advising.objects.get(student__user=user_of(ENDED_STUDENT))

    activities = Activity.objects.filter(advising=advising)
    assert activities.count() == 2
    assert set(activities.values_list("status", flat=True)) == {ActivityStatus.ARCHIVED}


def test_the_student_was_told_what_the_ending_archived(seeded):
    notification = Notification.objects.get(
        recipient=user_of(ENDED_STUDENT), type=NotificationType.ADVISING_ENDED
    )

    assert notification.payload["archived_activities"] == 2
    assert notification.payload["cancelled_meetings"] == 2


def test_the_ending_cancelled_the_future_meetings_by_the_system(seeded):
    advising = Advising.objects.get(student__user=user_of(ENDED_STUDENT))

    meetings = Meeting.objects.filter(advising=advising)
    assert meetings.count() == 2
    for meeting in meetings:
        assert meeting.status == MeetingStatus.CANCELLED
        assert meeting.cancelled_by_system is True
        assert meeting.cancellation_reason == SYSTEM_CANCELLATION_REASON


def test_the_whole_story_of_the_ended_advising_is_old_and_coherent(seeded):
    advising = Advising.objects.get(student__user=user_of(ENDED_STUDENT))

    for activity in Activity.objects.filter(advising=advising):
        assert activity.created_at > advising.started_at
        assert activity.created_at < advising.ended_at
        assert activity.deadline < advising.ended_at.date()


def test_the_other_course_student_only_finds_the_teacher_of_the_own_course(client, seeded):
    response = get(client, OTHER_COURSE_STUDENT, "/api/orientadores/busca")

    assert [item["id"] for item in response.json()["items"]] == [user_of(OTHER_COURSE_TEACHER).id]


def test_the_first_student_does_not_find_the_teacher_of_the_other_course(client, seeded):
    response = get(client, FIRST_STUDENT, "/api/orientadores/busca")

    assert user_of(OTHER_COURSE_TEACHER).id not in [item["id"] for item in response.json()["items"]]


def test_the_other_course_student_cannot_open_the_profile_of_a_teacher_of_another_course(
    client, seeded
):
    teacher_id = user_of(TEACHER).id

    response = get(client, OTHER_COURSE_STUDENT, f"/api/orientadores/{teacher_id}/perfil")

    assert response.status_code == 404


def test_the_other_course_student_cannot_ask_a_teacher_of_another_course(client, seeded):
    response = post_json(
        client,
        "/api/orientacao/solicitacoes",
        {"teacher_id": user_of(TEACHER).id},
        headers=bearer(user_of(OTHER_COURSE_STUDENT)),
    )

    assert response.status_code == 409
    assert "não orienta o seu curso" in response.json()["detail"]


@pytest.mark.parametrize("slug", [COMPLETE_STUDENT, FIRST_STUDENT, ENDED_STUDENT])
def test_the_other_course_teacher_cannot_open_the_profile_of_a_student_of_another_course(
    client, seeded, slug
):
    response = get(client, OTHER_COURSE_TEACHER, f"/api/alunos/{user_of(slug).id}/perfil")

    assert response.status_code == 404


@pytest.mark.parametrize(
    "url",
    ["/api/orientacao/orientandos", "/api/orientacao/solicitacoes", "/api/orientacao/historico"],
)
@pytest.mark.parametrize("slug", [OTHER_COURSE_TEACHER, EMPTY_TEACHER])
def test_the_teachers_without_students_see_the_empty_lists(client, seeded, slug, url):
    response = get(client, slug, url)

    assert response.json() == EMPTY


def test_the_other_course_student_has_no_history(client, seeded):
    assert get(client, OTHER_COURSE_STUDENT, "/api/orientacao/historico").json() == EMPTY


def test_running_it_again_duplicates_nothing(seeded):
    counts = (
        Advising.objects.count(),
        Activity.objects.count(),
        Meeting.objects.count(),
        ThesisTopic.objects.count(),
        Notification.objects.count(),
    )

    seed()

    assert counts == (
        Advising.objects.count(),
        Activity.objects.count(),
        Meeting.objects.count(),
        ThesisTopic.objects.count(),
        Notification.objects.count(),
    )
