import datetime
import threading
import time

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.activities.models import Activity
from apps.advising.models import Advising, AdvisingStatus
from apps.advising.services import close_advising
from tests.api.helpers import bearer, post_json
from tests.factories import make_advising, make_course, make_student, make_teacher

pytestmark = pytest.mark.django_db

URL = "/api/atividades"
TODAY_PLUS_A_WEEK = (timezone.localdate() + datetime.timedelta(days=7)).isoformat()


def payload(student, **overrides):
    body = {
        "student_id": student.user_id,
        "title": "Revisão bibliográfica",
        "description": "Entregar o capítulo 2 do TCC",
        "deadline": TODAY_PLUS_A_WEEK,
    }
    return {**body, **overrides}


def create_activity(client, user, body):
    return post_json(client, URL, body, headers=bearer(user))


def advised_student():
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    return student, teacher, make_advising(student=student, teacher=teacher)


def test_the_active_advisor_creates_a_pending_activity_for_the_advisee(client):
    student, teacher, advising = advised_student()

    response = create_activity(client, teacher.user, payload(student))

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "title", "description", "deadline", "status", "created_at"}
    assert body["title"] == "Revisão bibliográfica"
    assert body["description"] == "Entregar o capítulo 2 do TCC"
    assert body["deadline"] == TODAY_PLUS_A_WEEK
    assert body["status"] == "pending"
    activity = Activity.objects.get(pk=body["id"])
    assert activity.advising == advising
    assert activity.deleted_at is None


def test_the_title_and_the_description_are_trimmed(client):
    student, teacher, _ = advised_student()

    response = create_activity(
        client, teacher.user, payload(student, title="  Capítulo 2 \n", description="\t Texto  ")
    )

    assert response.status_code == 201
    assert response.json()["title"] == "Capítulo 2"
    assert response.json()["description"] == "Texto"
    assert Activity.objects.get().title == "Capítulo 2"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", ""),
        ("title", "   "),
        ("title", "x" * 201),
        ("title", " " + "x" * 201 + " "),
        ("description", ""),
        ("description", " \n "),
        ("description", "x" * 2001),
    ],
)
def test_an_invalid_title_or_description_is_refused_and_creates_nothing(client, field, value):
    student, teacher, _ = advised_student()

    response = create_activity(client, teacher.user, payload(student, **{field: value}))

    assert response.status_code == 422
    assert Activity.objects.count() == 0


@pytest.mark.parametrize(
    ("field", "value"), [("title", "x"), ("title", "x" * 200), ("description", "x" * 2000)]
)
def test_the_limits_of_the_title_and_of_the_description_are_accepted(client, field, value):
    student, teacher, _ = advised_student()

    assert (
        create_activity(client, teacher.user, payload(student, **{field: value})).status_code == 201
    )


def test_a_deadline_of_today_is_accepted_and_an_earlier_one_is_refused(client):
    student, teacher, _ = advised_student()
    today = timezone.localdate()

    refused = create_activity(
        client,
        teacher.user,
        payload(student, deadline=(today - datetime.timedelta(days=1)).isoformat()),
    )
    accepted = create_activity(client, teacher.user, payload(student, deadline=today.isoformat()))

    assert refused.status_code == 422
    assert accepted.status_code == 201
    assert accepted.json()["deadline"] == today.isoformat()
    assert Activity.objects.count() == 1


def test_a_far_away_deadline_has_no_upper_limit(client):
    student, teacher, _ = advised_student()

    response = create_activity(client, teacher.user, payload(student, deadline="2999-12-31"))

    assert response.status_code == 201


@pytest.mark.parametrize("deadline", ["amanhã", "2026-02-30", "2026-13-01", "", None, 20261120])
def test_an_invalid_deadline_is_refused(client, deadline):
    student, teacher, _ = advised_student()

    response = create_activity(client, teacher.user, payload(student, deadline=deadline))

    assert response.status_code == 422
    assert Activity.objects.count() == 0


@pytest.mark.parametrize("missing", ["student_id", "title", "description", "deadline"])
def test_a_missing_field_is_refused(client, missing):
    student, teacher, _ = advised_student()
    body = payload(student)
    del body[missing]

    response = create_activity(client, teacher.user, body)

    assert response.status_code == 422
    assert Activity.objects.count() == 0


def test_there_is_no_limit_of_activities_and_titles_can_repeat(client):
    student, teacher, advising = advised_student()

    responses = [create_activity(client, teacher.user, payload(student)) for _ in range(12)]

    assert {response.status_code for response in responses} == {201}
    assert len({response.json()["id"] for response in responses}) == 12
    assert Activity.objects.filter(advising=advising, title="Revisão bibliográfica").count() == 12


def test_a_teacher_without_an_active_advising_with_the_student_gets_the_same_404(client):
    student, owner, _ = advised_student()
    other = make_teacher(courses=[student.course])
    unlinked = make_student(course=student.course)
    ended_student = make_student(course=student.course)
    ended = make_advising(student=ended_student, teacher=other)
    Advising.objects.filter(pk=ended.pk).update(
        status=AdvisingStatus.ENDED, ended_at=timezone.now(), end_reason="Encerrada no teste."
    )

    responses = [
        create_activity(client, other.user, payload(student)),
        create_activity(client, other.user, payload(unlinked)),
        create_activity(client, other.user, payload(ended_student)),
        create_activity(client, other.user, payload(student, student_id=999999)),
    ]

    assert {response.status_code for response in responses} == {404}
    assert len({response.content for response in responses}) == 1
    assert Activity.objects.count() == 0


def test_a_student_is_refused_and_anonymous_is_unauthorized(client):
    student, _, _ = advised_student()

    assert create_activity(client, student.user, payload(student)).status_code == 403
    assert post_json(client, URL, payload(student)).status_code == 401
    assert Activity.objects.count() == 0


def count_queries(client, user, body):
    with CaptureQueriesContext(connection) as queries:
        response = create_activity(client, user, body)
    assert response.status_code == 201
    return len(queries)


def test_the_number_of_queries_does_not_depend_on_the_data_around(client):
    student, teacher, advising = advised_student()
    count_queries(client, teacher.user, payload(student))
    baseline = count_queries(client, teacher.user, payload(student))
    for _ in range(8):
        other_student, other_teacher, _ = advised_student()
        for _ in range(3):
            create_activity(client, other_teacher.user, payload(other_student))

    assert count_queries(client, teacher.user, payload(student)) == baseline


def test_the_openapi_documents_the_route_and_its_errors(client):
    operation = client.get("/api/openapi.json").json()["paths"]["/api/atividades"]["post"]

    assert {"201", "401", "403", "404", "422"} <= set(operation["responses"])


@pytest.mark.django_db(transaction=True)
def test_an_advising_that_ends_while_the_activity_is_being_created_gets_the_404_and_no_activity(
    client,
):
    student, teacher, advising = advised_student()
    holding_the_lock = threading.Event()
    failures = []

    def end_the_advising_while_holding_the_lock():
        try:
            with transaction.atomic():
                locked = Advising.objects.select_for_update().get(pk=advising.pk)
                holding_the_lock.set()
                time.sleep(0.5)
                close_advising(locked, "Encerrada durante a criação da atividade.")
        except Exception as error:  # noqa: BLE001 - reported to the main thread
            failures.append(error)
            holding_the_lock.set()
        finally:
            connection.close()

    ending = threading.Thread(target=end_the_advising_while_holding_the_lock)
    ending.start()
    assert holding_the_lock.wait(timeout=10)

    response = create_activity(client, teacher.user, payload(student))

    ending.join(timeout=10)
    assert failures == []
    assert response.status_code == 404
    assert Activity.objects.count() == 0
    assert Advising.objects.get(pk=advising.pk).status == AdvisingStatus.ENDED
