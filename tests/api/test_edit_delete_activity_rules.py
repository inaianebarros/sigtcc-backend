import datetime

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import UserType
from apps.activities.models import Activity, ActivityStatus
from apps.advising.models import Advising, AdvisingStatus
from tests.api.helpers import bearer, send_while_the_advising_ends
from tests.factories import make_advising, make_course, make_student, make_teacher, make_user

pytestmark = pytest.mark.django_db


def url(activity_id):
    return f"/api/atividades/{activity_id}"


def patch(client, user, activity_id, body=None):
    return client.patch(
        url(activity_id),
        data=body or {"title": "Novo título"},
        content_type="application/json",
        headers=bearer(user),
    )


def delete(client, user, activity_id, body=None):
    return client.delete(url(activity_id), headers=bearer(user))


ACTIONS = pytest.mark.parametrize("act", [patch, delete], ids=["edit", "delete"])


def advised_pair():
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    return student, teacher, make_advising(student=student, teacher=teacher)


def add_activity(advising, status=ActivityStatus.PENDING, **extra):
    return Activity.objects.create(
        advising=advising,
        title="Capítulo 2",
        description="Entregar o capítulo 2 do TCC",
        deadline=timezone.localdate() + datetime.timedelta(days=7),
        status=status,
        **extra,
    )


def untouched(activity):
    activity.refresh_from_db()
    return (activity.title, activity.status, activity.deleted_at)


@ACTIONS
def test_the_same_404_for_every_activity_the_teacher_does_not_advise(client, act):
    student, teacher, advising = advised_pair()
    other_teacher = make_teacher(courses=[student.course])
    theirs = add_activity(
        make_advising(student=make_student(course=student.course), teacher=other_teacher)
    )
    deleted = add_activity(advising, deleted_at=timezone.now())
    ended_advising = make_advising(student=make_student(course=student.course), teacher=teacher)
    ended = add_activity(ended_advising)
    Advising.objects.filter(pk=ended_advising.pk).update(
        status=AdvisingStatus.ENDED, ended_at=timezone.now(), end_reason="Encerrada no teste."
    )
    mine = add_activity(advising)

    responses = [
        act(client, teacher.user, theirs.id),
        act(client, teacher.user, deleted.id),
        act(client, teacher.user, ended.id),
        act(client, teacher.user, 999999),
        act(client, other_teacher.user, mine.id),
    ]

    assert {response.status_code for response in responses} == {404}
    assert len({response.content for response in responses}) == 1
    assert untouched(theirs)[2] is None and untouched(ended)[2] is None
    assert untouched(mine) == ("Capítulo 2", ActivityStatus.PENDING, None)


@ACTIONS
def test_a_completed_activity_answers_409_with_a_clear_message_and_nothing_changes(client, act):
    _, teacher, advising = advised_pair()
    activity = add_activity(advising, status=ActivityStatus.COMPLETED)

    response = act(client, teacher.user, activity.id)

    assert response.status_code == 409
    assert response.json() == {"detail": "Esta atividade já foi concluída e não pode ser alterada."}
    assert untouched(activity) == ("Capítulo 2", ActivityStatus.COMPLETED, None)


@ACTIONS
def test_the_404_comes_before_the_409_so_nothing_is_revealed(client, act):
    student, _, advising = advised_pair()
    stranger = make_teacher(courses=[student.course])
    completed = add_activity(advising, status=ActivityStatus.COMPLETED)

    response = act(client, stranger.user, completed.id)

    assert response.status_code == 404


@ACTIONS
def test_a_student_is_refused_an_administrator_too_and_anonymous_is_unauthorized(client, act):
    student, _, advising = advised_pair()
    activity = add_activity(advising)

    assert act(client, student.user, activity.id).status_code == 403
    assert act(client, make_user(UserType.ADMIN), activity.id).status_code == 403
    assert client.generic(act.__name__.upper(), url(activity.id)).status_code == 401
    assert untouched(activity) == ("Capítulo 2", ActivityStatus.PENDING, None)


@ACTIONS
def test_a_non_numeric_id_is_not_a_route(client, act):
    _, teacher, _ = advised_pair()

    assert act(client, teacher.user, "abc").status_code in (404, 405, 422)


@pytest.mark.django_db(transaction=True)
@ACTIONS
def test_an_advising_that_ends_during_the_action_gets_the_404_and_nothing_changes(client, act):
    _, teacher, advising = advised_pair()
    activity = add_activity(advising)

    response = send_while_the_advising_ends(
        advising, lambda: act(client, teacher.user, activity.id)
    )

    assert response.status_code == 404
    activity.refresh_from_db()
    assert activity.title == "Capítulo 2"
    assert activity.deleted_at is None
    assert activity.status == ActivityStatus.ARCHIVED
    assert Advising.objects.get(pk=advising.pk).status == AdvisingStatus.ENDED


def count_queries(client, user, act, activity_id):
    with CaptureQueriesContext(connection) as queries:
        response = act(client, user, activity_id)
    assert response.status_code in (200, 204)
    return len(queries)


@ACTIONS
def test_the_number_of_queries_does_not_depend_on_the_data_around(client, act):
    _, teacher, advising = advised_pair()
    baseline = count_queries(client, teacher.user, act, add_activity(advising).id)
    for _ in range(3):
        other_student, other_teacher, other_advising = advised_pair()
        for _ in range(4):
            add_activity(other_advising)
        add_activity(advising)

    assert count_queries(client, teacher.user, act, add_activity(advising).id) == baseline


def test_the_openapi_documents_the_routes_and_their_errors(client):
    operations = client.get("/api/openapi.json").json()["paths"]["/api/atividades/{activity_id}"]

    assert {"200", "401", "403", "404", "409", "422"} <= set(operations["patch"]["responses"])
    assert {"204", "401", "403", "404", "409"} <= set(operations["delete"]["responses"])
