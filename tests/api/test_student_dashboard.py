"""The student dashboard: one read-only summary of the active advising (RF50, RN35–RN37)."""

import datetime

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import UserType
from apps.activities.models import Activity, ActivityStatus
from apps.advising.models import (
    Advising,
    AdvisingRequest,
    AdvisingRequestStatus,
    ThesisTopic,
)
from apps.advising.services import close_advising
from apps.meetings.models import Meeting, MeetingStatus
from tests.api.helpers import bearer
from tests.factories import make_advising, make_course, make_student, make_teacher, make_user

pytestmark = pytest.mark.django_db

URL = "/api/painel/aluno"


def get(client, user):
    return client.get(URL, headers=bearer(user))


def today():
    return timezone.localdate()


def in_days(days):
    return today() + datetime.timedelta(days=days)


def advised_pair():
    course = make_course()
    teacher = make_teacher(courses=[course], full_name="Carlos Lima")
    student = make_student(course=course)
    return student, teacher, make_advising(student=student, teacher=teacher)


def add(advising, status=ActivityStatus.PENDING, days=7, title="Atividade", deleted=False):
    return Activity.objects.create(
        advising=advising,
        title=title,
        description="Descrição",
        deadline=in_days(days),
        status=status,
        deleted_at=timezone.now() if deleted else None,
    )


def add_many(advising, count, status):
    Activity.objects.bulk_create(
        Activity(
            advising=advising,
            title=f"A{index}",
            description="d",
            deadline=in_days(30),
            status=status,
        )
        for index in range(count)
    )


def test_a_student_with_an_active_advising_gets_the_whole_summary(client):
    student, teacher, advising = advised_pair()
    ThesisTopic.objects.create(advising=advising, description="Redes neurais em saúde")
    add(advising, ActivityStatus.PENDING, days=3, title="Pendente")
    add(advising, ActivityStatus.PENDING, days=-2, title="Atrasada")
    add(advising, ActivityStatus.IN_PROGRESS, days=5)
    add(advising, ActivityStatus.IN_REVIEW, days=9)
    add(advising, ActivityStatus.COMPLETED, days=-10)

    response = get(client, student.user)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "has_active_advising",
        "advising",
        "activities",
        "next_activities",
        "next_meetings",
        "last_request",
        "has_ended_advisings",
    }
    assert body["has_active_advising"] is True
    assert body["advising"] == {
        "teacher": {"id": teacher.user.id, "full_name": "Carlos Lima"},
        "thesis_topic": "Redes neurais em saúde",
    }
    assert body["activities"] == {
        "total": 5,
        "pending": 2,
        "in_progress": 1,
        "in_review": 1,
        "completed": 1,
        "overdue": 1,
        "progress_percent": 20,
    }
    assert body["last_request"] is None
    assert body["has_ended_advisings"] is False


def test_a_missing_thesis_topic_is_null(client):
    student, _, _ = advised_pair()

    body = get(client, student.user).json()

    assert body["advising"]["thesis_topic"] is None


def test_archived_deleted_and_other_advisings_activities_are_not_counted(client):
    student, teacher, advising = advised_pair()
    add(advising, ActivityStatus.PENDING)
    add(advising, ActivityStatus.ARCHIVED)
    add(advising, ActivityStatus.COMPLETED, deleted=True)
    add(advising, ActivityStatus.PENDING, days=-3, deleted=True)
    other = make_advising(teacher=teacher)
    add(other, ActivityStatus.PENDING)

    body = get(client, student.user).json()

    assert body["activities"]["total"] == 1
    assert body["activities"]["completed"] == 0
    assert body["activities"]["overdue"] == 0
    assert [item["status"] for item in body["next_activities"]["items"]] == ["pending"]


def test_an_ended_advising_is_never_shown_as_active_and_its_activities_do_not_count(client):
    student, teacher, advising = advised_pair()
    add(advising, ActivityStatus.COMPLETED)
    add(advising, ActivityStatus.PENDING)
    close_advising(advising, "Concluímos o ciclo de orientação.")

    body = get(client, student.user).json()

    assert body["has_active_advising"] is False
    assert body["advising"] is None
    assert body["activities"] is None
    assert body["next_activities"] is None
    assert body["next_meetings"] is None
    assert body["has_ended_advisings"] is True


def test_a_new_advising_starts_without_the_activities_of_the_ended_one(client):
    student, teacher, advising = advised_pair()
    add(advising, ActivityStatus.COMPLETED)
    close_advising(advising, "Concluímos o ciclo de orientação.")
    make_advising(student=student, teacher=make_teacher(courses=[student.course]))

    body = get(client, student.user).json()

    assert body["has_active_advising"] is True
    assert body["activities"]["total"] == 0
    assert body["has_ended_advisings"] is True


def test_progress_is_truncated_so_199_of_200_is_99(client):
    student, _, advising = advised_pair()
    add_many(advising, 199, ActivityStatus.COMPLETED)
    add_many(advising, 1, ActivityStatus.PENDING)

    body = get(client, student.user).json()

    assert body["activities"]["total"] == 200
    assert body["activities"]["progress_percent"] == 99


@pytest.mark.parametrize(("completed", "total", "expected"), [(1, 3, 33), (2, 3, 66), (3, 3, 100)])
def test_progress_percent_is_completed_over_total_truncated(client, completed, total, expected):
    student, _, advising = advised_pair()
    add_many(advising, completed, ActivityStatus.COMPLETED)
    add_many(advising, total - completed, ActivityStatus.IN_PROGRESS)

    body = get(client, student.user).json()

    assert body["activities"]["progress_percent"] == expected


def test_without_activities_there_is_no_percentage_and_no_division_by_zero(client):
    student, _, _ = advised_pair()

    response = get(client, student.user)

    assert response.status_code == 200
    body = response.json()
    assert body["activities"] == {
        "total": 0,
        "pending": 0,
        "in_progress": 0,
        "in_review": 0,
        "completed": 0,
        "overdue": 0,
        "progress_percent": None,
    }
    assert body["next_activities"] == {"items": [], "has_more": False}


def test_an_activity_due_today_is_not_late_and_one_due_yesterday_is_(client):
    student, _, advising = advised_pair()
    add(advising, ActivityStatus.PENDING, days=0, title="Hoje")
    add(advising, ActivityStatus.IN_PROGRESS, days=-1, title="Ontem")
    add(advising, ActivityStatus.COMPLETED, days=-5, title="Concluída antiga")

    body = get(client, student.user).json()

    assert body["activities"]["overdue"] == 1
    by_title = {item["title"]: item for item in body["next_activities"]["items"]}
    assert by_title["Hoje"]["overdue"] is False
    assert by_title["Ontem"]["overdue"] is True
    # Late is not a status: the activity keeps its own, and it still counts in it.
    assert by_title["Ontem"]["status"] == "in_progress"
    assert body["activities"]["in_progress"] == 1
    assert "Concluída antiga" not in by_title


def test_next_activities_are_open_ordered_by_deadline_late_first_and_limited_to_5(client):
    student, _, advising = advised_pair()
    add(advising, ActivityStatus.PENDING, days=10, title="D10")
    add(advising, ActivityStatus.IN_REVIEW, days=-4, title="Em revisão atrasada")
    add(advising, ActivityStatus.PENDING, days=2, title="D2")
    add(advising, ActivityStatus.IN_PROGRESS, days=-1, title="Atrasada ontem")
    add(advising, ActivityStatus.COMPLETED, days=-9, title="Concluída")
    add(advising, ActivityStatus.PENDING, days=5, title="D5")
    add(advising, ActivityStatus.PENDING, days=30, title="D30")

    body = get(client, student.user).json()

    titles = [item["title"] for item in body["next_activities"]["items"]]
    assert titles == ["Em revisão atrasada", "Atrasada ontem", "D2", "D5", "D10"]
    assert body["next_activities"]["has_more"] is True
    assert set(body["next_activities"]["items"][0]) == {
        "id",
        "title",
        "deadline",
        "status",
        "overdue",
    }
    assert body["activities"]["total"] == 7


def test_with_up_to_5_open_activities_there_is_no_more(client):
    student, _, advising = advised_pair()
    for days in range(5):
        add(advising, ActivityStatus.PENDING, days=days, title=f"D{days}")

    body = get(client, student.user).json()

    assert len(body["next_activities"]["items"]) == 5
    assert body["next_activities"]["has_more"] is False


def test_activities_with_the_same_deadline_keep_a_stable_order(client):
    student, _, advising = advised_pair()
    first = add(advising, ActivityStatus.PENDING, days=4, title="Primeira")
    second = add(advising, ActivityStatus.PENDING, days=4, title="Segunda")

    items = get(client, student.user).json()["next_activities"]["items"]

    assert [item["id"] for item in items] == [first.id, second.id]


def test_a_student_without_any_request_gets_no_last_request(client):
    student = make_student()

    body = get(client, student.user).json()

    assert body["has_active_advising"] is False
    assert body["last_request"] is None
    assert body["has_ended_advisings"] is False


@pytest.mark.parametrize(
    ("status", "age_days", "expected"),
    [
        (AdvisingRequestStatus.PENDING, 1, "pending"),
        (AdvisingRequestStatus.REJECTED, 1, "rejected"),
        (AdvisingRequestStatus.PENDING, 8, "unanswered"),
    ],
)
def test_without_an_active_advising_it_shows_the_last_request_with_its_effective_status(
    client, status, age_days, expected
):
    teacher = make_teacher(full_name="Carlos Lima")
    student = make_student()
    requested = AdvisingRequest.objects.create(student=student, teacher=teacher, status=status)
    AdvisingRequest.objects.filter(pk=requested.pk).update(
        requested_at=timezone.now() - datetime.timedelta(days=age_days)
    )

    body = get(client, student.user).json()

    assert body["has_active_advising"] is False
    assert body["last_request"]["status"] == expected
    assert body["last_request"]["teacher"] == {
        "id": teacher.user.id,
        "full_name": "Carlos Lima",
    }
    assert set(body["last_request"]) == {"id", "status", "requested_at", "teacher"}


def test_the_last_request_is_the_most_recent_one(client):
    student = make_student()
    old = AdvisingRequest.objects.create(
        student=student, teacher=make_teacher(), status=AdvisingRequestStatus.REJECTED
    )
    AdvisingRequest.objects.filter(pk=old.pk).update(
        requested_at=timezone.now() - datetime.timedelta(days=20)
    )
    AdvisingRequest.objects.create(
        student=student, teacher=make_teacher(), status=AdvisingRequestStatus.PENDING
    )

    body = get(client, student.user).json()

    assert body["last_request"]["status"] == "pending"


def test_an_accepted_request_of_an_ended_advising_is_not_shown_as_the_last_request(client):
    student, teacher, _ = advised_pair()
    accepted = AdvisingRequest.objects.create(
        student=student, teacher=teacher, status=AdvisingRequestStatus.ACCEPTED
    )
    advising = Advising.objects.get(student=student)
    Advising.objects.filter(pk=advising.pk).update(request=accepted)
    close_advising(Advising.objects.get(pk=advising.pk), "Concluímos o ciclo de orientação.")

    body = get(client, student.user).json()

    assert body["has_active_advising"] is False
    assert body["last_request"] is None
    assert body["has_ended_advisings"] is True


def test_with_an_active_advising_there_is_no_last_request(client):
    student, teacher, _ = advised_pair()
    AdvisingRequest.objects.create(
        student=student, teacher=teacher, status=AdvisingRequestStatus.ACCEPTED
    )

    body = get(client, student.user).json()

    assert body["last_request"] is None


def test_a_student_never_sees_the_data_of_another_student(client):
    student, teacher, advising = advised_pair()
    other = make_student(course=student.course)
    other_advising = make_advising(student=other, teacher=teacher)
    ThesisTopic.objects.create(advising=advising, description="Tema da Ana")
    ThesisTopic.objects.create(advising=other_advising, description="Tema do outro")
    add(advising, ActivityStatus.PENDING, title="Da Ana")
    add(other_advising, ActivityStatus.COMPLETED, title="Do outro")
    add(other_advising, ActivityStatus.COMPLETED, title="Do outro 2")

    body = get(client, student.user).json()

    assert body["advising"]["thesis_topic"] == "Tema da Ana"
    assert body["activities"]["total"] == 1
    assert [item["title"] for item in body["next_activities"]["items"]] == ["Da Ana"]


def test_anonymous_gets_401_and_a_teacher_or_an_administrator_gets_403(client):
    teacher = make_teacher()
    admin = make_user(UserType.ADMIN)

    assert client.get(URL).status_code == 401
    assert get(client, teacher.user).status_code == 403
    assert get(client, admin).status_code == 403


def test_the_number_of_queries_does_not_grow_with_the_activities(client):
    student, _, advising = advised_pair()
    ThesisTopic.objects.create(advising=advising, description="Tema")
    add(advising, ActivityStatus.PENDING)

    def queries_for_a_read():
        with CaptureQueriesContext(connection) as queries:
            assert get(client, student.user).status_code == 200
        return len(queries)

    few = queries_for_a_read()
    for index in range(30):
        add(advising, list(ActivityStatus)[index % 4], days=index - 10)
        add_meeting(advising, hours=index + 1)

    assert queries_for_a_read() == few


def test_the_dashboard_is_documented_in_the_openapi_schema(client):
    schema = client.get("/api/openapi.json").json()

    operation = schema["paths"]["/api/painel/aluno"]["get"]
    assert set(operation["responses"]) >= {"200", "401", "403"}
    assert operation["summary"]


def add_meeting(
    advising, status=MeetingStatus.ACCEPTED, hours=48, description="Reunião de orientação"
):
    return Meeting.objects.create(
        advising=advising,
        author=advising.teacher.user,
        scheduled_at=timezone.now() + datetime.timedelta(hours=hours),
        ends_at=timezone.now() + datetime.timedelta(hours=hours + 1),
        description=description,
        status=status,
        rejection_reason="Sem horário." if status == MeetingStatus.REJECTED else "",
    )


def test_the_next_meetings_are_the_accepted_future_ones_of_the_active_advising_in_order(client):
    student, teacher, advising = advised_pair()
    late = add_meeting(advising, hours=100, description="Última")
    soon = add_meeting(advising, hours=2, description="Primeira")
    middle = add_meeting(advising, hours=50, description="Do meio")

    body = get(client, student.user).json()

    meetings = body["next_meetings"]
    assert [item["id"] for item in meetings["items"]] == [soon.id, middle.id, late.id]
    assert meetings["has_more"] is False
    first = meetings["items"][0]
    assert set(first) == {"id", "scheduled_at", "ends_at", "teacher", "description"}
    assert first["teacher"] == {"id": teacher.user.id, "full_name": "Carlos Lima"}
    assert first["description"] == "Primeira"


def test_pending_refused_cancelled_and_past_meetings_do_not_appear(client):
    student, _, advising = advised_pair()
    add_meeting(advising, MeetingStatus.PENDING, hours=24)
    add_meeting(advising, MeetingStatus.REJECTED, hours=24)
    add_meeting(advising, MeetingStatus.CANCELLED, hours=24)
    add_meeting(advising, MeetingStatus.ACCEPTED, hours=-2)
    add_meeting(advising, MeetingStatus.PENDING, hours=-2)
    wanted = add_meeting(advising, MeetingStatus.ACCEPTED, hours=24)

    meetings = get(client, student.user).json()["next_meetings"]

    assert [item["id"] for item in meetings["items"]] == [wanted.id]


def test_the_meetings_of_other_advisings_never_appear(client):
    student, teacher, advising = advised_pair()
    mine = add_meeting(advising, hours=10)
    other = make_advising(teacher=teacher)
    add_meeting(other, hours=5)
    ended_student = make_student(course=student.course)
    ended = make_advising(student=ended_student, teacher=teacher)
    add_meeting(ended, hours=3)
    close_advising(ended, "Concluímos o ciclo de orientação.")

    meetings = get(client, student.user).json()["next_meetings"]

    assert [item["id"] for item in meetings["items"]] == [mine.id]


def test_the_meetings_are_limited_to_5_and_say_when_there_are_more(client):
    student, _, advising = advised_pair()
    for hours in range(1, 7):
        add_meeting(advising, hours=hours)

    meetings = get(client, student.user).json()["next_meetings"]

    assert len(meetings["items"]) == 5
    assert meetings["has_more"] is True


def test_exactly_5_meetings_have_no_more(client):
    student, _, advising = advised_pair()
    for hours in range(1, 6):
        add_meeting(advising, hours=hours)

    meetings = get(client, student.user).json()["next_meetings"]

    assert len(meetings["items"]) == 5
    assert meetings["has_more"] is False


def test_a_long_description_is_summarized_and_a_short_one_is_kept(client):
    student, _, advising = advised_pair()
    add_meeting(advising, hours=1, description="a" * 300)
    add_meeting(advising, hours=2, description="Curta")

    long_one, short_one = get(client, student.user).json()["next_meetings"]["items"]

    assert len(long_one["description"]) == 100
    assert long_one["description"].endswith("…")
    assert short_one["description"] == "Curta"


def test_the_meeting_time_comes_in_the_project_time_zone(client):
    student, _, advising = advised_pair()
    add_meeting(advising, hours=24)

    item = get(client, student.user).json()["next_meetings"]["items"][0]

    assert item["scheduled_at"].endswith("-03:00")


def test_without_meetings_the_list_is_empty_and_without_an_advising_it_is_null(client):
    student, _, _ = advised_pair()
    alone = make_student()

    assert get(client, student.user).json()["next_meetings"] == {"items": [], "has_more": False}
    assert get(client, alone.user).json()["next_meetings"] is None


def test_a_student_never_sees_the_meetings_of_another_student(client):
    student, teacher, advising = advised_pair()
    other = make_student(course=student.course)
    other_advising = make_advising(student=other, teacher=teacher)
    mine = add_meeting(advising, hours=10)
    add_meeting(other_advising, hours=5)
    add_meeting(other_advising, hours=6)

    meetings = get(client, student.user).json()["next_meetings"]

    assert [item["id"] for item in meetings["items"]] == [mine.id]
