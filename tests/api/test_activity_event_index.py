"""Every action of an activity records its own index in the timeline (RN49, #229).

The index is sequential inside the activity (the creation is 1), has no gaps or repeats, never
changes, and the student's status changes are recorded as events of their own.
"""

import threading
from unittest.mock import patch

import pytest
from django.core.files.storage import InMemoryStorage
from django.db import connection
from django.test import Client

from apps.activities.models import (
    Activity,
    Comment,
    Review,
    StatusChange,
    Submission,
)
from tests.api.activity_flow import (
    BASE,
    accept,
    advised_pair,
    change_status,
    comment,
    create_activity,
    deliver,
    refuse,
)
from tests.api.helpers import bearer, post_json
from tests.api.uploads import PDF, upload

pytestmark = pytest.mark.django_db


def last_index(activity):
    activity.refresh_from_db()
    return activity.last_event_index


def test_a_new_activity_is_the_first_event_of_its_timeline(client):
    student, teacher, _ = advised_pair()

    activity = create_activity(client, teacher, student)

    assert last_index(activity) == 1


def test_each_action_takes_the_next_index_whatever_its_type(client):
    student, teacher, _ = advised_pair()
    activity = create_activity(client, teacher, student)

    assert deliver(client, student, activity).status_code == 201
    assert comment(client, teacher.user, activity).status_code == 201
    assert change_status(client, student, activity, "in_progress").status_code == 200
    assert change_status(client, student, activity, "in_review").status_code == 200
    assert refuse(client, teacher, activity).status_code == 200
    assert comment(client, student.user, activity).status_code == 201

    assert Submission.objects.get().index == 2
    assert list(Comment.objects.order_by("id").values_list("index", flat=True)) == [3, 7]
    assert list(StatusChange.objects.order_by("id").values_list("index", flat=True)) == [4, 5]
    assert Review.objects.get().index == 6
    assert last_index(activity) == 7


def test_the_indexes_of_an_activity_have_no_gap_and_no_repeat_across_all_the_sources(client):
    student, teacher, _ = advised_pair()
    activity = create_activity(client, teacher, student)
    deliver(client, student, activity)
    comment(client, teacher.user, activity)
    change_status(client, student, activity, "in_review")
    accept(client, teacher, activity)

    indexes = [
        *Submission.objects.filter(activity=activity).values_list("index", flat=True),
        *Comment.objects.filter(activity=activity).values_list("index", flat=True),
        *StatusChange.objects.filter(activity=activity).values_list("index", flat=True),
        *Review.objects.filter(activity=activity).values_list("index", flat=True),
    ]

    assert sorted(indexes) == list(range(2, last_index(activity) + 1))


def test_the_indexes_are_counted_by_activity_and_never_change_afterwards(client):
    student, teacher, advising = advised_pair()
    first = create_activity(client, teacher, student)
    second = create_activity(client, teacher, student)
    comment(client, teacher.user, first)
    comment(client, teacher.user, first)
    comment(client, teacher.user, second)
    before = list(Comment.objects.order_by("id").values_list("index", flat=True))

    client.patch(
        f"{BASE}/{first.id}",
        data={"title": "Novo título"},
        content_type="application/json",
        headers=bearer(teacher.user),
    )
    comment(client, student.user, first)

    assert before == [2, 3, 2]
    assert list(Comment.objects.order_by("id").values_list("index", flat=True))[:3] == before
    assert last_index(first) == 4
    assert last_index(second) == 2


@pytest.mark.parametrize(
    ("moves", "recorded"),
    [
        (["in_progress"], ["in_progress"]),
        (["in_progress", "pending"], ["in_progress", "pending"]),
        (["in_review"], ["in_review"]),
        (["in_progress", "in_review"], ["in_progress", "in_review"]),
    ],
)
def test_each_status_change_of_the_student_is_recorded_with_the_new_status(client, moves, recorded):
    student, teacher, _ = advised_pair()
    activity = create_activity(client, teacher, student)
    deliver(client, student, activity)

    for status in moves:
        assert change_status(client, student, activity, status).status_code == 200

    changes = list(StatusChange.objects.order_by("id"))
    assert [change.status for change in changes] == recorded
    assert {change.author_id for change in changes} == {student.user_id}
    assert all(change.changed_at is not None for change in changes)


def test_asking_for_the_status_the_activity_already_has_records_nothing(client):
    student, teacher, _ = advised_pair()
    activity = create_activity(client, teacher, student)

    assert change_status(client, student, activity, "pending").status_code == 200

    assert not StatusChange.objects.exists()
    assert last_index(activity) == 1


def test_sending_for_review_again_after_a_refusal_is_recorded_again(client):
    student, teacher, _ = advised_pair()
    activity = create_activity(client, teacher, student)
    deliver(client, student, activity)
    change_status(client, student, activity, "in_review")
    refuse(client, teacher, activity)

    assert change_status(client, student, activity, "in_review").status_code == 200

    assert list(StatusChange.objects.order_by("id").values_list("status", flat=True)) == [
        "in_review",
        "in_review",
    ]


def test_accepting_and_refusing_are_reviews_and_never_status_changes(client):
    student, teacher, _ = advised_pair()
    refused = create_activity(client, teacher, student)
    accepted = create_activity(client, teacher, student)
    for activity in (refused, accepted):
        deliver(client, student, activity)
        change_status(client, student, activity, "in_review")
    StatusChange.objects.all().delete()

    refuse(client, teacher, refused)
    accept(client, teacher, accepted)

    assert not StatusChange.objects.exists()
    assert Review.objects.count() == 2


def test_a_refused_delivery_leaves_no_index_behind(client):
    student, teacher, _ = advised_pair()
    activity = create_activity(client, teacher, student)

    invalid = client.post(
        f"{BASE}/{activity.id}/entregas",
        data={"files": [upload("virus.exe", b"MZ")]},
        headers=bearer(student.user),
    )

    assert invalid.status_code == 422
    assert last_index(activity) == 1
    assert not Submission.objects.exists()


def test_a_storage_failure_when_refusing_leaves_no_index_behind(client):
    student, teacher, _ = advised_pair()
    activity = create_activity(client, teacher, student)
    deliver(client, student, activity)
    change_status(client, student, activity, "in_review")
    before = last_index(activity)

    with patch.object(InMemoryStorage, "save", side_effect=OSError("o armazenamento caiu")):
        response = refuse(client, teacher, activity, files=[upload("a.pdf", PDF)])

    assert response.status_code == 503
    assert last_index(activity) == before
    assert not Review.objects.exists()


def test_a_comment_refused_for_a_completed_activity_leaves_no_index_behind(client):
    student, teacher, _ = advised_pair()
    activity = create_activity(client, teacher, student)
    deliver(client, student, activity)
    change_status(client, student, activity, "in_review")
    accept(client, teacher, activity)
    before = last_index(activity)

    assert comment(client, student.user, activity).status_code == 409

    assert last_index(activity) == before


@pytest.mark.django_db(transaction=True)
def test_simultaneous_actions_get_distinct_consecutive_indexes():
    student, teacher, advising = advised_pair()
    activity = Activity.objects.create(
        advising=advising, title="Cap", description="d", deadline="2099-12-31"
    )
    failures = []

    def post_comment(number):
        try:
            response = post_json(
                Client(),
                f"{BASE}/{activity.id}/comentarios",
                {"text": f"Comentário {number}"},
                headers=bearer(teacher.user),
            )
            assert response.status_code == 201
        except Exception as error:  # noqa: BLE001 - reported to the main thread
            failures.append(error)
        finally:
            connection.close()

    threads = [threading.Thread(target=post_comment, args=(number,)) for number in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert failures == []
    indexes = sorted(Comment.objects.filter(activity=activity).values_list("index", flat=True))
    assert indexes == [2, 3, 4, 5, 6]
    activity.refresh_from_db()
    assert activity.last_event_index == 6
