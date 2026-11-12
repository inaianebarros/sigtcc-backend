import datetime
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import UserType
from apps.advising.models import Advising, AdvisingStatus
from apps.meetings.models import Meeting, MeetingStatus
from tests.api.helpers import bearer
from tests.factories import (
    make_advising,
    make_course,
    make_meeting,
    make_student,
    make_teacher,
    make_user,
)

pytestmark = pytest.mark.django_db

URL = "/api/reunioes/calendario"
SANTAREM = ZoneInfo("America/Santarem")


def calendar(client, user, **params):
    return client.get(URL, data=params, headers=bearer(user))


def ids_of(response):
    return [item["id"] for item in response.json()]


def at(day, hour=10, minute=0):
    return datetime.datetime(day.year, day.month, day.day, hour, minute, tzinfo=SANTAREM)


def day_in(days):
    return (timezone.now().astimezone(SANTAREM) + datetime.timedelta(days=days)).date()


def period(first, last):
    return {"date_from": first.isoformat(), "date_to": last.isoformat()}


def advised_pair():
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    return student, teacher, make_advising(student=student, teacher=teacher)


def test_it_returns_every_meeting_of_the_period_ordered_by_start_then_id_without_paging(client):
    student, _, advising = advised_pair()
    base = day_in(5)
    late = make_meeting(advising, scheduled_at=at(base, 15))
    early = make_meeting(advising, scheduled_at=at(base, 8))
    tie_a = make_meeting(advising, scheduled_at=at(base, 11))
    tie_b = make_meeting(advising, scheduled_at=at(base, 11))
    outside = make_meeting(advising, scheduled_at=at(day_in(30)))

    response = calendar(client, student.user, **period(base, base + datetime.timedelta(days=2)))

    assert response.status_code == 200
    assert ids_of(response) == [early.id, tie_a.id, tie_b.id, late.id]
    assert outside.id not in ids_of(response)


def test_more_than_a_page_of_meetings_comes_in_one_response(client):
    student, _, advising = advised_pair()
    base = day_in(4)
    for minute in range(30):
        make_meeting(advising, scheduled_at=at(base, 9, minute))

    response = calendar(client, student.user, **period(base, base))

    assert len(response.json()) == 30


def test_the_dates_are_inclusive_and_read_in_santarem_near_midnight(client):
    student, _, advising = advised_pair()
    first, last = day_in(6), day_in(8)
    just_after_midnight = make_meeting(advising, scheduled_at=at(first, 0, 0))
    just_before_midnight = make_meeting(advising, scheduled_at=at(last, 23, 59))
    day_before = make_meeting(advising, scheduled_at=at(first - datetime.timedelta(days=1), 23, 59))
    day_after = make_meeting(advising, scheduled_at=at(last + datetime.timedelta(days=1), 0, 0))

    response = calendar(client, student.user, **period(first, last))

    assert ids_of(response) == [just_after_midnight.id, just_before_midnight.id]
    assert day_before.id not in ids_of(response)
    assert day_after.id not in ids_of(response)


def test_the_window_may_have_exactly_42_days_and_not_more(client):
    student, _, advising = advised_pair()
    first = day_in(1)
    last_day = make_meeting(advising, scheduled_at=at(first + datetime.timedelta(days=41)))

    accepted = calendar(client, student.user, **period(first, first + datetime.timedelta(days=41)))
    refused = calendar(client, student.user, **period(first, first + datetime.timedelta(days=42)))

    assert ids_of(accepted) == [last_day.id]
    assert refused.status_code == 422
    assert [e["loc"][-1] for e in refused.json()["detail"]] == ["date_to"]
    assert "42 dias" in refused.json()["detail"][0]["msg"]


def test_an_end_before_the_start_is_refused(client):
    student, _, _ = advised_pair()

    response = calendar(client, student.user, **period(day_in(5), day_in(4)))

    assert response.status_code == 422
    assert [e["loc"][-1] for e in response.json()["detail"]] == ["date_from"]


def test_the_last_day_the_calendar_can_represent_is_refused_instead_of_failing(client):
    student, _, _ = advised_pair()

    response = calendar(client, student.user, date_from="9999-12-01", date_to="9999-12-31")

    assert response.status_code == 422
    assert [e["loc"][-1] for e in response.json()["detail"]] == ["date_to"]
    assert (
        calendar(client, student.user, date_from="9999-12-01", date_to="9999-12-30").status_code
        == 200
    )


@pytest.mark.parametrize("missing", ["date_from", "date_to"])
def test_both_dates_are_required(client, missing):
    student, _, _ = advised_pair()
    params = period(day_in(1), day_in(2))
    del params[missing]

    assert calendar(client, student.user, **params).status_code == 422


def test_meetings_of_ended_advisings_are_included_and_other_users_never_appear(client):
    student, teacher, advising = advised_pair()
    base = day_in(3)
    own = make_meeting(advising, scheduled_at=at(base))
    other_student, _, other_advising = advised_pair()
    make_meeting(other_advising, scheduled_at=at(base))
    Advising.objects.filter(pk=advising.pk).update(
        status=AdvisingStatus.ENDED, ended_at=timezone.now(), end_reason="Encerrada no teste."
    )
    current = make_advising(student=student, teacher=make_teacher(courses=[student.course]))
    current_meeting = make_meeting(current, scheduled_at=at(base, 14))
    window = period(base, base)

    assert ids_of(calendar(client, student.user, **window)) == [own.id, current_meeting.id]
    assert ids_of(calendar(client, teacher.user, **window)) == [own.id]
    assert ids_of(calendar(client, other_student.user, **window)) != ids_of(
        calendar(client, student.user, **window)
    )


def situations_setup():
    student, _, advising = advised_pair()
    base = day_in(0)
    pending = make_meeting(advising, scheduled_at=timezone.now() + datetime.timedelta(hours=2))
    accepted = make_meeting(
        advising, MeetingStatus.ACCEPTED, scheduled_at=timezone.now() + datetime.timedelta(hours=3)
    )
    rejected = make_meeting(
        advising, MeetingStatus.REJECTED, scheduled_at=timezone.now() + datetime.timedelta(hours=4)
    )
    cancelled = make_meeting(
        advising, MeetingStatus.CANCELLED, scheduled_at=timezone.now() + datetime.timedelta(hours=5)
    )
    Meeting.objects.filter(pk=cancelled.pk).update(cancellation_reason="Imprevisto.")
    held = make_meeting(
        advising, MeetingStatus.ACCEPTED, scheduled_at=timezone.now() - datetime.timedelta(hours=3)
    )
    expired = make_meeting(advising, scheduled_at=timezone.now() - datetime.timedelta(hours=2))
    window = period(base - datetime.timedelta(days=1), base + datetime.timedelta(days=1))
    return (
        student,
        window,
        {
            "pending": pending,
            "accepted": accepted,
            "rejected": rejected,
            "cancelled": cancelled,
            "held": held,
            "expired": expired,
        },
    )


def test_one_or_several_situations_filter_including_the_derived_held_and_expired(client):
    student, window, by_name = situations_setup()

    def wanted(*names):
        return calendar(client, student.user, **window, situation=list(names))

    assert set(ids_of(wanted("held"))) == {by_name["held"].id}
    assert set(ids_of(wanted("expired"))) == {by_name["expired"].id}
    assert set(ids_of(wanted("pending", "cancelled"))) == {
        by_name["pending"].id,
        by_name["cancelled"].id,
    }
    assert len(ids_of(calendar(client, student.user, **window))) == 6
    assert wanted("talvez").status_code == 422


def test_each_item_has_start_end_situation_and_the_capabilities(client):
    student, _, advising = advised_pair()
    base = day_in(4)
    meeting = make_meeting(advising, author=student.user, scheduled_at=at(base, 9))

    (item,) = calendar(client, student.user, **period(base, base)).json()

    assert item["id"] == meeting.id
    assert item["scheduled_at"] == at(base, 9).isoformat()
    assert item["ends_at"] == at(base, 10).isoformat()
    assert item["situation"] == "pending"
    assert item["counterpart"]["id"] == advising.teacher.user_id
    assert (item["can_answer"], item["can_edit"], item["can_cancel"]) == (False, True, True)
    assert {"status", "author", "description", "rejection_reason", "cancellation_reason"} <= set(
        item
    )


def test_the_teacher_hides_selected_advisees_and_a_new_one_shows_up_by_default(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    ana = make_student(course=course)
    bruno = make_student(course=course)
    base = day_in(3)
    from_ana = make_meeting(make_advising(student=ana, teacher=teacher), scheduled_at=at(base, 9))
    from_bruno = make_meeting(
        make_advising(student=bruno, teacher=teacher), scheduled_at=at(base, 10)
    )
    window = period(base, base)

    everybody = calendar(client, teacher.user, **window)
    without_ana = calendar(client, teacher.user, **window, hidden_student_ids=[ana.user_id])
    without_both = calendar(
        client, teacher.user, **window, hidden_student_ids=[ana.user_id, bruno.user_id]
    )
    newcomer = make_student(course=course)
    from_newcomer = make_meeting(
        make_advising(student=newcomer, teacher=teacher), scheduled_at=at(base, 11)
    )
    after_hiding = calendar(client, teacher.user, **window, hidden_student_ids=[ana.user_id])

    assert ids_of(everybody) == [from_ana.id, from_bruno.id]
    assert ids_of(without_ana) == [from_bruno.id]
    assert ids_of(without_both) == []
    assert ids_of(after_hiding) == [from_bruno.id, from_newcomer.id]


def test_the_student_cannot_hide_students(client):
    student, _, _ = advised_pair()

    response = calendar(
        client, student.user, **period(day_in(1), day_in(2)), hidden_student_ids=[student.user_id]
    )

    assert response.status_code == 422
    assert [e["loc"][-1] for e in response.json()["detail"]] == ["hidden_student_ids"]


def test_the_situation_and_the_advisee_filters_combine(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    ana = make_student(course=course)
    bruno = make_student(course=course)
    base = day_in(3)
    ana_advising = make_advising(student=ana, teacher=teacher)
    bruno_advising = make_advising(student=bruno, teacher=teacher)
    make_meeting(ana_advising, scheduled_at=at(base, 9))
    ana_accepted = make_meeting(ana_advising, MeetingStatus.ACCEPTED, scheduled_at=at(base, 10))
    make_meeting(bruno_advising, MeetingStatus.ACCEPTED, scheduled_at=at(base, 11))

    response = calendar(
        client,
        teacher.user,
        **period(base, base),
        situation=["accepted"],
        hidden_student_ids=[bruno.user_id],
    )

    assert ids_of(response) == [ana_accepted.id]


def test_only_students_and_teachers_with_a_token_can_read(client):
    window = period(day_in(1), day_in(2))

    assert client.get(URL, data=window).status_code == 401
    assert calendar(client, make_user(UserType.ADMIN), **window).status_code == 403


def test_the_number_of_queries_does_not_grow_with_the_number_of_meetings(client):
    student, _, advising = advised_pair()
    base = day_in(3)
    make_meeting(advising, scheduled_at=at(base, 9))

    def count():
        with CaptureQueriesContext(connection) as queries:
            response = client.get(URL, data=period(base, base), headers=bearer(student.user))
        assert response.status_code == 200
        return len(queries)

    few = count()
    for minute in range(20):
        make_meeting(advising, scheduled_at=at(base, 10, minute))

    assert count() == few


def test_the_openapi_documents_the_reading_and_its_limit(client):
    schema = client.get("/api/openapi.json").json()

    operation = schema["paths"]["/api/reunioes/calendario"]["get"]
    assert {p["name"] for p in operation["parameters"]} == {
        "date_from",
        "date_to",
        "situation",
        "hidden_student_ids",
    }
    assert "42 dias" in operation["description"]
    assert "Não usada pela tela" in schema["paths"]["/api/reunioes"]["get"]["description"]
