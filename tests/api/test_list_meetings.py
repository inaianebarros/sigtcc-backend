import datetime
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import UserType
from apps.advising.models import Advising, AdvisingStatus
from apps.meetings.models import Meeting, MeetingStatus
from tests.api.helpers import bearer, post_json
from tests.factories import (
    make_advising,
    make_course,
    make_meeting,
    make_student,
    make_teacher,
    make_user,
)

pytestmark = pytest.mark.django_db

URL = "/api/reunioes"
SANTAREM = ZoneInfo("America/Santarem")


def list_meetings(client, user, **params):
    return client.get(URL, data=params, headers=bearer(user))


def ids_of(response):
    return [item["id"] for item in response.json()["items"]]


def advised_pair():
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    return student, teacher, make_advising(student=student, teacher=teacher)


def in_days(days, hour=10, minute=0):
    """A moment in Santarém `days` from today (negative for the past), at a round time."""
    day = (timezone.now().astimezone(SANTAREM) + datetime.timedelta(days=days)).date()
    return datetime.datetime(day.year, day.month, day.day, hour, minute, tzinfo=SANTAREM)


def end_advising(advising):
    Advising.objects.filter(pk=advising.pk).update(
        status=AdvisingStatus.ENDED, ended_at=timezone.now(), end_reason="Encerrada no teste."
    )


def test_the_student_lists_a_meeting_with_the_item_shape(client):
    student, teacher, advising = advised_pair()
    meeting = make_meeting(advising, author=student.user, scheduled_at=in_days(3))

    response = list_meetings(client, student.user)

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    item = body["items"][0]
    assert set(item) == {
        "id",
        "scheduled_at",
        "ends_at",
        "description",
        "status",
        "situation",
        "author",
        "counterpart",
        "rejection_reason",
        "cancellation_reason",
        "cancelled_by_system",
        "can_answer",
        "can_edit",
        "can_cancel",
        "marked_held",
        "can_mark_held",
    }
    assert item["id"] == meeting.id
    assert item["scheduled_at"] == in_days(3).isoformat()
    assert item["description"] == meeting.description
    assert item["status"] == "pending"
    assert item["situation"] == "pending"
    assert item["author"] == {
        "id": student.user_id,
        "full_name": student.user.full_name,
        "type": "student",
    }
    assert item["counterpart"] == {"id": teacher.user_id, "full_name": teacher.user.full_name}
    assert item["rejection_reason"] == ""
    assert item["cancellation_reason"] == ""
    assert item["cancelled_by_system"] is False


def test_the_teacher_lists_with_the_student_as_the_counterpart(client):
    student, teacher, advising = advised_pair()
    make_meeting(advising, author=teacher.user, scheduled_at=in_days(2))

    item = list_meetings(client, teacher.user).json()["items"][0]

    assert item["author"]["type"] == "teacher"
    assert item["counterpart"] == {"id": student.user_id, "full_name": student.user.full_name}


def test_each_user_lists_only_their_own_meetings(client):
    student, teacher, advising = advised_pair()
    mine = make_meeting(advising, author=student.user, scheduled_at=in_days(2))
    other_student = make_student(course=student.course)
    other_teacher = make_teacher(courses=[student.course])
    other = make_meeting(
        make_advising(student=other_student, teacher=other_teacher),
        author=other_student.user,
        scheduled_at=in_days(2),
    )
    same_teacher = make_meeting(
        make_advising(student=make_student(course=student.course), teacher=teacher),
        scheduled_at=in_days(2),
    )

    assert ids_of(list_meetings(client, student.user)) == [mine.id]
    assert ids_of(list_meetings(client, other_teacher.user)) == [other.id]
    assert sorted(ids_of(list_meetings(client, teacher.user))) == sorted([mine.id, same_teacher.id])


def test_the_history_of_ended_advisings_is_included_with_the_cancellation_data(client):
    student, teacher, advising = advised_pair()
    held = make_meeting(
        advising, author=student.user, status=MeetingStatus.ACCEPTED, scheduled_at=in_days(-5)
    )
    by_system = make_meeting(advising, author=student.user, scheduled_at=in_days(3))
    Meeting.objects.filter(pk=by_system.pk).update(
        status=MeetingStatus.CANCELLED,
        cancelled_by_system=True,
        cancellation_reason="Cancelada pelo sistema.",
    )
    end_advising(advising)
    new = make_advising(student=student, teacher=make_teacher(courses=[student.course]))
    current = make_meeting(new, author=student.user, scheduled_at=in_days(4))

    items = {item["id"]: item for item in list_meetings(client, student.user).json()["items"]}

    assert set(items) == {held.id, by_system.id, current.id}
    assert items[by_system.id]["cancelled_by_system"] is True
    assert items[by_system.id]["cancellation_reason"] == "Cancelada pelo sistema."
    assert items[by_system.id]["situation"] == "cancelled"


def test_a_user_without_meetings_gets_an_empty_page(client):
    student = make_student()

    response = list_meetings(client, student.user)

    assert response.status_code == 200
    assert response.json() == {"items": [], "count": 0}


def test_the_item_carries_no_email_or_registration_number(client):
    student, teacher, advising = advised_pair()
    make_meeting(advising, author=student.user, scheduled_at=in_days(2))

    for user in (student.user, teacher.user):
        raw = list_meetings(client, user).content.decode()
        for secret in (
            student.user.email,
            teacher.user.email,
            student.registration_number,
            teacher.registration_number,
        ):
            assert secret not in raw


@pytest.mark.parametrize(
    ("status", "days", "expected"),
    [
        (MeetingStatus.PENDING, 2, "pending"),
        (MeetingStatus.ACCEPTED, 2, "accepted"),
        (MeetingStatus.REJECTED, 2, "rejected"),
        (MeetingStatus.REJECTED, -2, "rejected"),
        (MeetingStatus.CANCELLED, 2, "cancelled"),
        (MeetingStatus.CANCELLED, -2, "cancelled"),
        (MeetingStatus.ACCEPTED, -2, "held"),
        (MeetingStatus.PENDING, -2, "expired"),
    ],
)
def test_the_situation_is_derived_from_the_status_and_the_clock(client, status, days, expected):
    student, _, advising = advised_pair()
    meeting = make_meeting(advising, author=student.user, status=status, scheduled_at=in_days(days))
    if status == MeetingStatus.CANCELLED:
        Meeting.objects.filter(pk=meeting.pk).update(cancellation_reason="Imprevisto.")

    item = list_meetings(client, student.user).json()["items"][0]

    assert item["situation"] == expected
    assert item["status"] == status.value


def test_a_meeting_becomes_past_at_its_moment_and_the_situation_is_not_stored(client):
    student, _, advising = advised_pair()
    just_passed = make_meeting(
        advising,
        author=student.user,
        status=MeetingStatus.ACCEPTED,
        scheduled_at=timezone.now() - datetime.timedelta(seconds=1),
    )
    about_to = make_meeting(
        advising,
        author=student.user,
        status=MeetingStatus.ACCEPTED,
        scheduled_at=timezone.now() + datetime.timedelta(minutes=5),
    )

    items = {item["id"]: item for item in list_meetings(client, student.user).json()["items"]}

    assert items[just_passed.id]["situation"] == "held"
    assert items[about_to.id]["situation"] == "accepted"
    assert Meeting.objects.get(pk=just_passed.pk).status == MeetingStatus.ACCEPTED
    assert not hasattr(Meeting, "situation")


@pytest.mark.parametrize(
    "situation", ["pending", "accepted", "rejected", "cancelled", "held", "expired"]
)
def test_the_situation_filter_keeps_only_that_situation(client, situation):
    student, _, advising = advised_pair()
    wanted = {
        "pending": (MeetingStatus.PENDING, 2),
        "accepted": (MeetingStatus.ACCEPTED, 2),
        "rejected": (MeetingStatus.REJECTED, 2),
        "cancelled": (MeetingStatus.CANCELLED, 2),
        "held": (MeetingStatus.ACCEPTED, -2),
        "expired": (MeetingStatus.PENDING, -2),
    }
    created = {}
    for name, (status, days) in wanted.items():
        created[name] = make_meeting(
            advising, author=student.user, status=status, scheduled_at=in_days(days)
        )
    Meeting.objects.filter(pk=created["cancelled"].pk).update(cancellation_reason="Imprevisto.")

    response = list_meetings(client, student.user, situation=situation)

    assert ids_of(response) == [created[situation].id]
    assert response.json()["count"] == 1


@pytest.mark.parametrize("value", ["pendente", "realizada", "", "PENDING", "all"])
def test_an_invalid_situation_is_refused(client, value):
    student, _, advising = advised_pair()
    make_meeting(advising, author=student.user, scheduled_at=in_days(2))

    assert list_meetings(client, student.user, situation=value).status_code == 422


def test_the_period_is_inclusive_and_read_in_santarem(client):
    student, _, advising = advised_pair()
    day = in_days(10).date()
    start = datetime.datetime(day.year, day.month, day.day, 0, 0, tzinfo=SANTAREM)
    inside = [
        make_meeting(advising, author=student.user, scheduled_at=start),
        make_meeting(
            advising,
            author=student.user,
            scheduled_at=start + datetime.timedelta(hours=23, minutes=59),
        ),
    ]
    before = make_meeting(
        advising, author=student.user, scheduled_at=start - datetime.timedelta(minutes=1)
    )
    after = make_meeting(
        advising, author=student.user, scheduled_at=start + datetime.timedelta(days=1)
    )

    on_the_day = list_meetings(
        client, student.user, date_from=day.isoformat(), date_to=day.isoformat()
    )
    from_only = list_meetings(client, student.user, date_from=day.isoformat())
    to_only = list_meetings(client, student.user, date_to=day.isoformat())

    assert sorted(ids_of(on_the_day)) == sorted(meeting.id for meeting in inside)
    assert after.id in ids_of(from_only) and before.id not in ids_of(from_only)
    assert before.id in ids_of(to_only) and after.id not in ids_of(to_only)


@pytest.mark.parametrize(
    "params",
    [
        {"date_from": "amanhã"},
        {"date_to": "2026-02-30"},
        {"date_from": "2026-12-31", "date_to": "2026-01-01"},
        {"situation": "foo"},
        {"student_id": "abc"},
    ],
)
def test_an_invalid_filter_value_is_refused(client, params):
    _, teacher, advising = advised_pair()
    make_meeting(advising, scheduled_at=in_days(2))

    assert list_meetings(client, teacher.user, **params).status_code == 422


def test_the_student_filter_is_for_the_teacher_and_stays_inside_the_scope(client):
    student, teacher, advising = advised_pair()
    other = make_advising(student=make_student(course=student.course), teacher=teacher)
    mine = make_meeting(advising, scheduled_at=in_days(2))
    theirs = make_meeting(other, scheduled_at=in_days(3))
    stranger = make_meeting(
        make_advising(
            student=make_student(course=student.course),
            teacher=make_teacher(courses=[student.course]),
        ),
        scheduled_at=in_days(2),
    )

    assert ids_of(list_meetings(client, teacher.user, student_id=student.user_id)) == [mine.id]
    assert ids_of(list_meetings(client, teacher.user, student_id=other.student.user_id)) == [
        theirs.id
    ]
    assert (
        ids_of(list_meetings(client, teacher.user, student_id=stranger.advising.student.user_id))
        == []
    )
    assert ids_of(list_meetings(client, teacher.user, student_id=999999)) == []


def test_a_student_who_sends_the_student_id_gets_a_validation_error(client):
    student, _, advising = advised_pair()
    make_meeting(advising, author=student.user, scheduled_at=in_days(2))

    response = list_meetings(client, student.user, student_id=student.user_id)

    assert response.status_code == 422
    assert "student_id" in [error["loc"][-1] for error in response.json()["detail"]]


def test_the_filters_combine_with_and(client):
    student, teacher, advising = advised_pair()
    other = make_advising(student=make_student(course=student.course), teacher=teacher)
    target = make_meeting(advising, status=MeetingStatus.ACCEPTED, scheduled_at=in_days(5))
    make_meeting(advising, status=MeetingStatus.PENDING, scheduled_at=in_days(5))
    make_meeting(advising, status=MeetingStatus.ACCEPTED, scheduled_at=in_days(20))
    make_meeting(other, status=MeetingStatus.ACCEPTED, scheduled_at=in_days(5))

    response = list_meetings(
        client,
        teacher.user,
        situation="accepted",
        date_from=in_days(4).date().isoformat(),
        date_to=in_days(6).date().isoformat(),
        student_id=student.user_id,
    )

    assert ids_of(response) == [target.id]


def test_the_order_is_upcoming_nearest_first_then_past_newest_first_then_id(client):
    student, _, advising = advised_pair()
    far = make_meeting(advising, scheduled_at=in_days(30))
    near = make_meeting(advising, scheduled_at=in_days(1))
    tied_first = make_meeting(advising, scheduled_at=in_days(5))
    tied_second = make_meeting(advising, scheduled_at=in_days(5))
    recent = make_meeting(advising, status=MeetingStatus.ACCEPTED, scheduled_at=in_days(-1))
    old = make_meeting(advising, status=MeetingStatus.ACCEPTED, scheduled_at=in_days(-40))
    tied_past_first = make_meeting(
        advising, status=MeetingStatus.PENDING, scheduled_at=in_days(-10)
    )
    tied_past_second = make_meeting(
        advising, status=MeetingStatus.PENDING, scheduled_at=in_days(-10)
    )
    Meeting.objects.filter(pk__in=[tied_first.pk, tied_past_first.pk]).update(description="editada")

    assert ids_of(list_meetings(client, student.user)) == [
        near.id,
        tied_first.id,
        tied_second.id,
        far.id,
        recent.id,
        tied_past_first.id,
        tied_past_second.id,
        old.id,
    ]


def test_the_list_is_paginated_and_the_total_follows_the_filter(client):
    student, _, advising = advised_pair()
    created = [make_meeting(advising, scheduled_at=in_days(day)) for day in range(1, 26)]
    make_meeting(advising, status=MeetingStatus.REJECTED, scheduled_at=in_days(50))

    first = list_meetings(client, student.user)
    second = list_meetings(client, student.user, limit=20, offset=20)
    small = list_meetings(client, student.user, limit=5, offset=10, situation="pending")

    assert first.json()["count"] == 26
    assert len(ids_of(first)) == 20
    assert len(ids_of(second)) == 6
    assert ids_of(small) == [meeting.id for meeting in created[10:15]]
    assert small.json()["count"] == 25
    assert list_meetings(client, student.user, limit=0).status_code == 422
    assert list_meetings(client, student.user, limit=101).status_code == 422
    assert list_meetings(client, student.user, offset=-1).status_code == 422


ACTION_CASES = [
    (status, days, author_is_student, active, actor_is_student)
    for status in (
        MeetingStatus.PENDING,
        MeetingStatus.ACCEPTED,
        MeetingStatus.REJECTED,
        MeetingStatus.CANCELLED,
    )
    for days in (2, -2)
    for author_is_student in (True, False)
    for active in (True, False)
    for actor_is_student in (True, False)
]


def build_case(status, days, author_is_student, active):
    student, teacher, advising = advised_pair()
    author = student.user if author_is_student else teacher.user
    meeting = make_meeting(advising, author=author, status=status, scheduled_at=in_days(days))
    if status == MeetingStatus.CANCELLED:
        Meeting.objects.filter(pk=meeting.pk).update(cancellation_reason="Imprevisto.")
    if not active:
        end_advising(advising)
    return student, teacher, meeting


@pytest.mark.parametrize(
    ("status", "days", "author_is_student", "active", "actor_is_student"), ACTION_CASES
)
def test_the_allowed_actions_match_what_the_answer_the_edit_and_the_cancel_really_accept(
    client, status, days, author_is_student, active, actor_is_student
):
    def actor_of(student, teacher):
        return (student if actor_is_student else teacher).user

    student, teacher, meeting = build_case(status, days, author_is_student, active)
    item = list_meetings(client, actor_of(student, teacher)).json()["items"][0]
    flags = (item["can_answer"], item["can_edit"], item["can_cancel"])

    accepted = []
    for call in (
        lambda user, m: post_json(
            client, f"{URL}/{m.id}/responder", {"action": "accept"}, headers=bearer(user)
        ),
        lambda user, m: client.patch(
            f"{URL}/{m.id}",
            data={"description": "Nova"},
            content_type="application/json",
            headers=bearer(user),
        ),
        lambda user, m: post_json(
            client, f"{URL}/{m.id}/cancelar", {"reason": "Imprevisto."}, headers=bearer(user)
        ),
    ):
        student, teacher, fresh = build_case(status, days, author_is_student, active)
        accepted.append(call(actor_of(student, teacher), fresh).status_code == 200)

    assert flags == tuple(accepted)


def test_an_ended_advising_allows_no_action_and_a_future_pending_one_allows_the_right_ones(client):
    student, teacher, advising = advised_pair()
    make_meeting(advising, author=student.user, scheduled_at=in_days(3))

    as_student = list_meetings(client, student.user).json()["items"][0]
    as_teacher = list_meetings(client, teacher.user).json()["items"][0]

    assert (as_student["can_answer"], as_student["can_edit"], as_student["can_cancel"]) == (
        False,
        True,
        True,
    )
    assert (as_teacher["can_answer"], as_teacher["can_edit"], as_teacher["can_cancel"]) == (
        True,
        False,
        False,
    )


def test_an_administrator_is_refused_and_anonymous_is_unauthorized(client):
    assert list_meetings(client, make_user(UserType.ADMIN)).status_code == 403
    assert client.get(URL).status_code == 401


def count_queries(client, user):
    with CaptureQueriesContext(connection) as queries:
        response = list_meetings(client, user, limit=100)
    assert response.status_code == 200
    return len(queries)


@pytest.mark.parametrize("role", ["student", "teacher"])
def test_the_number_of_queries_does_not_grow_with_the_number_of_meetings(client, role):
    student, teacher, advising = advised_pair()
    make_meeting(advising, author=student.user, scheduled_at=in_days(2))
    user = student.user if role == "student" else teacher.user
    few = count_queries(client, user)

    for _ in range(4):
        other = make_advising(student=make_student(course=student.course), teacher=teacher)
        for number in range(6):
            make_meeting(other, scheduled_at=in_days(number - 3), status=MeetingStatus.ACCEPTED)
    make_meeting(advising, author=teacher.user, scheduled_at=in_days(-4))

    assert count_queries(client, user) == few


def test_the_openapi_documents_the_list_with_its_filters_and_errors(client):
    operation = client.get("/api/openapi.json").json()["paths"]["/api/reunioes"]["get"]

    names = {parameter["name"] for parameter in operation["parameters"]}
    assert {"situation", "date_from", "date_to", "student_id", "limit", "offset"} <= names
    assert {"200", "401", "403", "422"} <= set(operation["responses"])


def test_the_openapi_documents_the_six_situations_of_the_item(client):
    schemas = client.get("/api/openapi.json").json()["components"]["schemas"]

    situation = schemas["MeetingItemOut"]["properties"]["situation"]
    values = schemas["MeetingSituation"]["enum"] if "$ref" in situation else situation["enum"]
    assert set(values) == {"pending", "accepted", "rejected", "cancelled", "held", "expired"}
