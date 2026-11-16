import datetime

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.advising.models import Advising, AdvisingRequest, AdvisingRequestStatus, AdvisingStatus
from tests.api.helpers import bearer
from tests.factories import make_course, make_student, make_teacher

pytestmark = pytest.mark.django_db

URL = "/api/orientacao/minhas-solicitacoes"


def read(client, user, **params):
    return client.get(URL, params, headers=bearer(user))


def parse(value):
    return datetime.datetime.fromisoformat(value)


def millis(moment):
    return moment.replace(microsecond=moment.microsecond // 1000 * 1000)


def send(
    student, teacher, status=AdvisingRequestStatus.REJECTED, requested_at=None, responded_at=None
):
    """The student has one pending request at most, so the default is an answered one."""
    request = AdvisingRequest.objects.create(student=student, teacher=teacher, status=status)
    changes = {"requested_at": requested_at, "responded_at": responded_at}
    AdvisingRequest.objects.filter(pk=request.pk).update(
        **{field: value for field, value in changes.items() if value is not None}
    )
    request.refresh_from_db()
    return request


@pytest.fixture
def student():
    return make_student(course=make_course())


def teacher_for(student, **kwargs):
    return make_teacher(courses=[student.course], **kwargs)


def test_it_needs_a_login_and_a_student(client, student):
    assert client.get(URL).status_code == 401
    assert read(client, teacher_for(student).user).status_code == 403


def test_a_student_sees_only_their_requests_newest_first(client, student):
    now = timezone.now()
    older = send(student, teacher_for(student), requested_at=now - datetime.timedelta(days=5))
    newer = send(student, teacher_for(student), requested_at=now - datetime.timedelta(days=1))
    send(make_student(course=student.course), teacher_for(student))

    body = read(client, student.user).json()

    assert body["count"] == 2
    assert [item["id"] for item in body["items"]] == [newer.id, older.id]


def test_each_item_brings_the_teacher_with_the_institute(client, student):
    teacher = teacher_for(student, full_name="Marta Rocha")
    send(student, teacher)

    item = read(client, student.user).json()["items"][0]

    assert item["teacher"] == {
        "id": teacher.user_id,
        "full_name": "Marta Rocha",
        "institute": {"id": student.course.institute_id, "name": student.course.institute.name},
    }


def test_the_list_has_ten_per_page_and_the_count_of_all(client, student):
    for _ in range(12):
        send(student, teacher_for(student))

    first = read(client, student.user).json()
    second = read(client, student.user, offset=10).json()
    beyond = read(client, student.user, offset=30).json()

    assert first["count"] == 12
    assert len(first["items"]) == 10
    assert len(second["items"]) == 2
    assert beyond["items"] == []


def test_the_name_filter_ignores_case_and_accents_and_the_count_follows_it(client, student):
    send(student, teacher_for(student, full_name="José Antônio Lima"))
    send(student, teacher_for(student, full_name="Marta Rocha"))

    body = read(client, student.user, name="  JOSE antonio ").json()

    assert body["count"] == 1
    assert body["items"][0]["teacher"]["full_name"] == "José Antônio Lima"
    assert read(client, student.user, name="").json()["count"] == 2
    assert read(client, student.user, name="zzz").json()["count"] == 0


def test_the_name_filter_has_a_limit_of_150_characters(client, student):
    assert read(client, student.user, name="a" * 151).status_code == 422
    assert read(client, student.user, name="a" * 150).status_code == 200


def test_a_pending_request_ends_at_the_deadline(client, student):
    requested_at = timezone.now() - datetime.timedelta(days=2)
    request = send(
        student, teacher_for(student), AdvisingRequestStatus.PENDING, requested_at=requested_at
    )

    item = read(client, student.user).json()["items"][0]

    assert item["id"] == request.id
    assert item["status"] == "pending"
    assert parse(item["expires_at"]) == millis(request.requested_at + datetime.timedelta(days=7))


def test_a_pending_request_past_the_deadline_is_an_unanswered_one(client, student):
    requested_at = timezone.now() - datetime.timedelta(days=8)
    send(student, teacher_for(student), AdvisingRequestStatus.PENDING, requested_at=requested_at)

    item = read(client, student.user).json()["items"][0]

    assert item["status"] == "unanswered"
    assert parse(item["expires_at"]) == millis(requested_at + datetime.timedelta(days=7))


def test_an_answered_request_has_no_deadline(client, student):
    send(student, teacher_for(student), responded_at=timezone.now())

    item = read(client, student.user).json()["items"][0]

    assert item["status"] == "rejected"
    assert item["expires_at"] is None
    assert item["responded_at"] is not None
    assert item["advising"] is None


def test_an_accepted_request_brings_the_advising_it_created(client, student):
    teacher = teacher_for(student)
    request = send(student, teacher, status=AdvisingRequestStatus.ACCEPTED)
    advising = Advising.objects.create(student=student, teacher=teacher, request=request)

    item = read(client, student.user).json()["items"][0]

    assert item["status"] == "accepted"
    assert item["advising"]["id"] == advising.id
    assert item["advising"]["status"] == "active"
    assert item["advising"]["ended_at"] is None


def test_an_ended_advising_brings_the_end_date(client, student):
    teacher = teacher_for(student)
    request = send(student, teacher, status=AdvisingRequestStatus.ACCEPTED)
    advising = Advising.objects.create(student=student, teacher=teacher, request=request)
    ended_at = timezone.now()
    Advising.objects.filter(pk=advising.pk).update(
        status=AdvisingStatus.ENDED, ended_at=ended_at, end_reason="Encerrada no teste."
    )

    item = read(client, student.user).json()["items"][0]

    assert item["advising"]["status"] == "ended"
    assert parse(item["advising"]["ended_at"]) == millis(ended_at)


def count_queries(client, user):
    with CaptureQueriesContext(connection) as queries:
        response = client.get(URL, headers=bearer(user))
    assert response.status_code == 200
    return len(queries)


def test_the_number_of_queries_does_not_grow_with_the_requests(client, student):
    send(student, teacher_for(student))
    few = count_queries(client, student.user)

    for _ in range(5):
        request = send(student, teacher_for(student), AdvisingRequestStatus.ACCEPTED)
        advising = Advising.objects.create(
            student=student, teacher=request.teacher, request=request
        )
        Advising.objects.filter(pk=advising.pk).update(
            status=AdvisingStatus.ENDED, ended_at=timezone.now(), end_reason="Encerrada no teste."
        )

    assert count_queries(client, student.user) == few
