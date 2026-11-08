import datetime
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import UserType
from apps.advising.models import Advising, AdvisingStatus
from apps.meetings.models import Meeting, MeetingStatus
from tests.api.helpers import bearer, post_json, send_while_the_advising_ends
from tests.factories import (
    make_advising,
    make_course,
    make_student,
    make_teacher,
    make_user,
)

pytestmark = pytest.mark.django_db

URL = "/api/reunioes"
SANTAREM = ZoneInfo("America/Santarem")


def local_now():
    return timezone.now().astimezone(SANTAREM)


def at(days=0, hours=0, minutes=0):
    """A date and a time (HH:MM) in Santarém, a given distance from now."""
    moment = local_now() + datetime.timedelta(days=days, hours=hours, minutes=minutes)
    return moment.date().isoformat(), moment.strftime("%H:%M")


def payload(**overrides):
    date, time = at(days=3)
    body = {"date": date, "time": time, "description": "Revisar o capítulo 2 do TCC"}
    return {**body, **overrides}


def create_meeting(client, user, body):
    return post_json(client, URL, body, headers=bearer(user))


def advised_student():
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    return student, teacher, make_advising(student=student, teacher=teacher)


def test_the_student_requests_a_meeting_with_the_active_advisor(client):
    student, _, advising = advised_student()
    body = payload(date="2099-12-31", time="14:30")

    response = create_meeting(client, student.user, body)

    assert response.status_code == 201
    result = response.json()
    assert set(result) == {"id", "scheduled_at", "description", "status", "author"}
    assert result["scheduled_at"] == "2099-12-31T14:30:00-03:00"
    assert result["description"] == "Revisar o capítulo 2 do TCC"
    assert result["status"] == "pending"
    assert result["author"] == {
        "id": student.user_id,
        "full_name": student.user.full_name,
        "type": "student",
    }
    meeting = Meeting.objects.get(pk=result["id"])
    assert meeting.advising == advising
    assert meeting.author == student.user
    assert meeting.status == MeetingStatus.PENDING
    assert meeting.scheduled_at == datetime.datetime(2099, 12, 31, 14, 30, tzinfo=SANTAREM)


def test_the_time_may_come_with_seconds_and_the_date_is_read_in_santarem(client):
    student, _, _ = advised_student()

    response = create_meeting(client, student.user, payload(date="2099-01-01", time="23:59:30"))

    assert response.status_code == 201
    assert response.json()["scheduled_at"] == "2099-01-01T23:59:30-03:00"


def test_a_meeting_in_the_past_or_right_now_is_refused_and_nothing_is_created(client):
    student, _, _ = advised_student()
    past_date, past_time = at(minutes=-1)
    far_past_date, far_past_time = at(days=-30)
    now_date, now_time = at()

    for date, time in (
        (past_date, past_time),
        (far_past_date, far_past_time),
        ("2000-01-01", "00:00"),
    ):
        response = create_meeting(client, student.user, payload(date=date, time=time))
        assert response.status_code == 422
        assert "date" in [error["loc"][-1] for error in response.json()["detail"]]
    # O minuto de agora já passou (a hora vem sem segundos): também é recusado.
    assert (
        create_meeting(client, student.user, payload(date=now_date, time=now_time)).status_code
        == 422
    )
    assert Meeting.objects.count() == 0


def test_a_meeting_one_minute_ahead_is_accepted_and_there_is_no_upper_limit(client):
    student, _, _ = advised_student()
    soon_date, soon_time = at(minutes=2)

    assert (
        create_meeting(client, student.user, payload(date=soon_date, time=soon_time)).status_code
        == 201
    )
    assert (
        create_meeting(client, student.user, payload(date="2999-12-31", time="23:59")).status_code
        == 201
    )


@pytest.mark.parametrize(
    "description",
    ["", "   ", "\n\t ", "x" * 501, " " + "x" * 501 + " "],
)
def test_an_invalid_description_is_refused_and_nothing_is_created(client, description):
    student, _, _ = advised_student()

    response = create_meeting(client, student.user, payload(description=description))

    assert response.status_code == 422
    assert Meeting.objects.count() == 0


@pytest.mark.parametrize("description", ["x", "x" * 500])
def test_the_limits_of_the_description_are_accepted(client, description):
    student, _, _ = advised_student()

    assert create_meeting(client, student.user, payload(description=description)).status_code == 201


def test_the_description_is_trimmed(client):
    student, _, _ = advised_student()

    response = create_meeting(client, student.user, payload(description="  Pauta da reunião \n"))

    assert response.json()["description"] == "Pauta da reunião"
    assert Meeting.objects.get().description == "Pauta da reunião"


@pytest.mark.parametrize(
    "overrides",
    [
        {"date": "amanhã"},
        {"date": "2099-02-30"},
        {"date": ""},
        {"date": None},
        {"time": "25:00"},
        {"time": "14h30"},
        {"time": ""},
        {"time": None},
    ],
)
def test_an_invalid_date_or_time_is_refused(client, overrides):
    student, _, _ = advised_student()

    response = create_meeting(client, student.user, payload(**overrides))

    assert response.status_code == 422
    assert Meeting.objects.count() == 0


@pytest.mark.parametrize("missing", ["date", "time", "description"])
def test_a_missing_field_is_refused(client, missing):
    student, _, _ = advised_student()
    body = payload()
    del body[missing]

    response = create_meeting(client, student.user, body)

    assert response.status_code == 422
    assert Meeting.objects.count() == 0


def test_a_student_who_sends_the_student_id_gets_a_validation_error(client):
    student, _, _ = advised_student()

    response = create_meeting(client, student.user, payload(student_id=student.user_id))

    assert response.status_code == 422
    assert "student_id" in [error["loc"][-1] for error in response.json()["detail"]]
    assert Meeting.objects.count() == 0


def test_an_invalid_body_is_validated_before_the_advising_is_checked(client):
    student = make_student()

    assert create_meeting(client, student.user, payload(description="")).status_code == 422
    assert create_meeting(client, student.user, payload(date="2000-01-01")).status_code == 422


def test_a_student_without_an_active_advising_gets_a_409(client):
    student = make_student()

    response = create_meeting(client, student.user, payload())

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Você precisa ter um orientador ativo para solicitar uma reunião."
    }
    assert Meeting.objects.count() == 0


def test_a_student_whose_only_advising_ended_gets_a_409(client):
    student, _, advising = advised_student()
    Advising.objects.filter(pk=advising.pk).update(
        status=AdvisingStatus.ENDED, ended_at=timezone.now(), end_reason="Encerrada no teste."
    )

    assert create_meeting(client, student.user, payload()).status_code == 409
    assert Meeting.objects.count() == 0


def test_a_new_advising_after_an_ended_one_receives_the_meeting(client):
    student, _, old = advised_student()
    Advising.objects.filter(pk=old.pk).update(
        status=AdvisingStatus.ENDED, ended_at=timezone.now(), end_reason="Encerrada no teste."
    )
    new = make_advising(student=student, teacher=make_teacher(courses=[student.course]))

    response = create_meeting(client, student.user, payload())

    assert response.status_code == 201
    assert Meeting.objects.get(pk=response.json()["id"]).advising == new


def test_two_meetings_at_the_same_time_are_allowed(client):
    student, _, advising = advised_student()
    body = payload(date="2099-06-01", time="10:00")

    assert create_meeting(client, student.user, body).status_code == 201
    assert create_meeting(client, student.user, body).status_code == 201
    assert Meeting.objects.filter(advising=advising).count() == 2


def test_a_teacher_cannot_use_the_route_yet_an_administrator_too_and_anonymous_is_unauthorized(
    client,
):
    _, teacher, _ = advised_student()

    assert create_meeting(client, teacher.user, payload()).status_code == 403
    assert create_meeting(client, make_user(UserType.ADMIN), payload()).status_code == 403
    assert post_json(client, URL, payload()).status_code == 401
    assert Meeting.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_an_advising_that_ends_while_the_meeting_is_requested_gets_the_409_and_no_meeting(client):
    student, _, advising = advised_student()

    response = send_while_the_advising_ends(
        advising, lambda: create_meeting(client, student.user, payload())
    )

    assert response.status_code == 409
    assert Meeting.objects.count() == 0
    assert Advising.objects.get(pk=advising.pk).status == AdvisingStatus.ENDED


def count_queries(client, user):
    with CaptureQueriesContext(connection) as queries:
        response = create_meeting(client, user, payload())
    assert response.status_code == 201
    return len(queries)


def test_the_number_of_queries_does_not_depend_on_the_data_around(client):
    student, _, _ = advised_student()
    count_queries(client, student.user)
    baseline = count_queries(client, student.user)
    for _ in range(6):
        other, _, _ = advised_student()
        for _ in range(3):
            create_meeting(client, other.user, payload())

    assert count_queries(client, student.user) == baseline


def test_the_openapi_documents_the_route_and_its_errors(client):
    operation = client.get("/api/openapi.json").json()["paths"]["/api/reunioes"]["post"]

    assert {"201", "401", "403", "409", "422"} <= set(operation["responses"])
