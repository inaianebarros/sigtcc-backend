"""The timeline of an activity, read by index (RN49, #230)."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import UserType
from apps.activities.models import Activity, ActivityStatus, Review, ReviewFile
from apps.advising.models import Advising
from apps.advising.services import close_advising
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
from tests.api.helpers import bearer
from tests.api.uploads import PDF, PNG, upload
from tests.factories import make_advising, make_student, make_teacher, make_user

pytestmark = pytest.mark.django_db

NOT_FOUND = {"detail": "Atividade não encontrada."}


def timeline_url(activity_id):
    return f"{BASE}/{activity_id}/linha-do-tempo"


def timeline(client, user, activity, **params):
    headers = bearer(user) if user else {}
    return client.get(timeline_url(activity.id), params, headers=headers)


def indexes(response):
    return [item["index"] for item in response.json()["items"]]


def types(response):
    return [item["type"] for item in response.json()["items"]]


def a_full_story(client):
    """create, deliver, comment, in progress, review, refusal with a file, deliver again, comment,
    review again, acceptance: ten events, the creation first."""
    student, teacher, _ = advised_pair()
    activity = create_activity(client, teacher, student)  # 1
    deliver(client, student, activity)  # 2
    comment(client, teacher.user, activity, "Comece pela introdução.")  # 3
    change_status(client, student, activity, "in_progress")  # 4
    change_status(client, student, activity, "in_review")  # 5
    refuse(client, teacher, activity, files=[upload("notas.png", PNG)])  # 6
    deliver(client, student, activity)  # 7
    comment(client, student.user, activity, "Ajustei.")  # 8
    change_status(client, student, activity, "in_review")  # 9
    accept(client, teacher, activity)  # 10
    return student, teacher, activity


def test_the_timeline_lists_every_event_from_the_latest_to_the_creation(client):
    student, _, activity = a_full_story(client)

    response = timeline(client, student.user, activity)

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 10
    assert body["has_more"] is False
    assert indexes(response) == [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
    assert types(response) == [
        "review",
        "sent_for_review",
        "comment",
        "delivery",
        "review",
        "sent_for_review",
        "status_change",
        "comment",
        "delivery",
        "created",
    ]


def test_the_student_and_the_active_advisor_read_the_same_timeline(client):
    student, teacher, activity = a_full_story(client)

    as_student = timeline(client, student.user, activity).json()
    as_teacher = timeline(client, teacher.user, activity).json()

    assert as_student == as_teacher


def test_every_event_carries_the_common_fields_and_its_author(client):
    student, teacher, activity = a_full_story(client)

    items = {
        item["index"]: item for item in timeline(client, student.user, activity).json()["items"]
    }

    for item in items.values():
        assert {"index", "type", "id", "occurred_at", "author"} <= set(item)
        assert set(item["author"]) == {"id", "full_name", "type"}
    assert items[1]["author"] == {
        "id": teacher.user_id,
        "full_name": teacher.user.full_name,
        "type": "teacher",
    }
    assert items[2]["author"]["type"] == "student"
    assert items[3]["author"]["type"] == "teacher"
    assert items[4]["author"]["id"] == student.user_id
    assert items[6]["author"]["id"] == teacher.user_id
    assert items[1]["id"] == activity.id


def test_the_creation_event_has_nothing_beyond_the_common_fields(client):
    student, _, activity = a_full_story(client)

    created = timeline(client, student.user, activity, before=2).json()["items"][0]

    assert set(created) == {"index", "type", "id", "occurred_at", "author"}


def test_a_delivery_carries_its_version_and_its_files(client):
    student, _, activity = a_full_story(client)

    items = {
        item["index"]: item for item in timeline(client, student.user, activity).json()["items"]
    }

    first, second = items[2], items[7]
    assert (first["version"], second["version"]) == (1, 2)
    assert set(first) == {
        "index",
        "type",
        "id",
        "occurred_at",
        "author",
        "version",
        "files",
        "awaiting_review",
    }
    assert first["files"] == [
        {
            "id": first["files"][0]["id"],
            "file_name": "a.pdf",
            "size_bytes": len(PDF),
            "content_type": "application/pdf",
        }
    ]


def test_the_status_changes_carry_the_new_status(client):
    student, _, activity = a_full_story(client)

    items = {
        item["index"]: item for item in timeline(client, student.user, activity).json()["items"]
    }

    assert items[4]["type"] == "status_change"
    assert items[4]["status"] == "in_progress"
    assert items[5]["type"] == "sent_for_review"
    assert "status" not in items[5]


def test_a_comment_carries_its_text_and_its_author(client):
    student, teacher, activity = a_full_story(client)

    items = {
        item["index"]: item for item in timeline(client, student.user, activity).json()["items"]
    }

    assert items[3]["text"] == "Comece pela introdução."
    assert items[8]["text"] == "Ajustei."
    assert items[8]["author"]["id"] == student.user_id


def test_a_review_carries_the_decision_the_justification_the_version_and_the_attachments(client):
    student, teacher, activity = a_full_story(client)

    items = {
        item["index"]: item for item in timeline(client, student.user, activity).json()["items"]
    }

    refusal, acceptance = items[6], items[10]
    assert refusal["decision"] == "rejected"
    assert refusal["justification"] == "Refazer."
    assert refusal["version"] == 1
    review = Review.objects.get(pk=refusal["id"])
    attached = ReviewFile.objects.get(review=review)
    assert refusal["files"] == [
        {
            "id": attached.id,
            "file_name": "notas.png",
            "size_bytes": len(PNG),
            "content_type": "image/png",
        }
    ]
    assert (acceptance["decision"], acceptance["justification"], acceptance["files"]) == (
        "accepted",
        "",
        [],
    )
    assert acceptance["version"] == 2


def test_the_delivery_awaits_evaluation_only_while_it_is_the_latest_and_in_review(client):
    student, teacher, _ = advised_pair()
    activity = create_activity(client, teacher, student)
    deliver(client, student, activity)
    change_status(client, student, activity, "in_review")

    def awaiting():
        items = timeline(client, student.user, activity).json()["items"]
        return {
            item["index"]: item["awaiting_review"] for item in items if item["type"] == "delivery"
        }

    assert awaiting() == {2: True}
    refuse(client, teacher, activity)
    assert awaiting() == {2: False}
    deliver(client, student, activity)
    assert awaiting() == {2: False, 5: False}
    change_status(client, student, activity, "in_review")
    assert awaiting() == {2: False, 5: True}
    accept(client, teacher, activity)
    assert awaiting() == {2: False, 5: False}


def test_the_page_size_is_10_by_default_and_before_pages_without_repeating_or_skipping(client):
    student, teacher, _ = advised_pair()
    activity = create_activity(client, teacher, student)
    for number in range(24):
        comment(client, teacher.user, activity, f"Comentário {number}")

    first = timeline(client, student.user, activity)
    second = timeline(client, student.user, activity, before=indexes(first)[-1])
    third = timeline(client, student.user, activity, before=indexes(second)[-1])

    assert first.json()["count"] == second.json()["count"] == third.json()["count"] == 25
    assert indexes(first) == list(range(25, 15, -1))
    assert indexes(second) == list(range(15, 5, -1))
    assert indexes(third) == [5, 4, 3, 2, 1]
    assert [r.json()["has_more"] for r in (first, second, third)] == [True, True, False]


def test_limit_changes_the_page_size_up_to_100(client):
    student, teacher, _ = advised_pair()
    activity = create_activity(client, teacher, student)
    for _ in range(14):
        comment(client, teacher.user, activity)

    small = timeline(client, student.user, activity, limit=3)
    everything = timeline(client, student.user, activity, limit=100)

    assert indexes(small) == [15, 14, 13]
    assert small.json()["has_more"] is True
    assert len(everything.json()["items"]) == 15
    assert everything.json()["has_more"] is False


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 101}, {"limit": "x"}, {"before": 0}, {"before": -3}, {"before": "x"}],
)
def test_an_invalid_limit_or_before_is_rejected_with_422(client, params):
    student, teacher, _ = advised_pair()
    activity = create_activity(client, teacher, student)

    assert timeline(client, student.user, activity, **params).status_code == 422


@pytest.mark.parametrize("before", [1, 2])
def test_before_the_first_event_there_is_nothing_more(client, before):
    student, teacher, _ = advised_pair()
    activity = create_activity(client, teacher, student)

    body = timeline(client, student.user, activity, before=before).json()

    assert body["has_more"] is False
    assert body["count"] == 1
    assert [item["index"] for item in body["items"]] == ([] if before == 1 else [1])


def test_before_above_the_latest_event_starts_at_the_latest(client):
    student, teacher, _ = advised_pair()
    activity = create_activity(client, teacher, student)
    comment(client, teacher.user, activity)

    assert indexes(timeline(client, student.user, activity, before=999)) == [2, 1]


@pytest.mark.parametrize("status", [ActivityStatus.IN_REVIEW, ActivityStatus.COMPLETED])
def test_the_timeline_reads_in_any_visible_status(client, status):
    student, teacher, _ = advised_pair()
    activity = create_activity(client, teacher, student)
    Activity.objects.filter(pk=activity.pk).update(status=status)

    assert timeline(client, student.user, activity).status_code == 200
    assert timeline(client, teacher.user, activity).status_code == 200


def hide_it(activity):
    Activity.objects.filter(pk=activity.pk).update(deleted_at=timezone.now())


def archive_it(activity):
    Activity.objects.filter(pk=activity.pk).update(status=ActivityStatus.ARCHIVED)


def end_the_advising(activity):
    close_advising(Advising.objects.get(pk=activity.advising_id), "Encerrada para o teste.")


@pytest.mark.parametrize("who", ["student", "advisor"])
@pytest.mark.parametrize("hide", [hide_it, archive_it, end_the_advising])
def test_a_deleted_archived_or_ended_activity_gets_the_same_404(client, hide, who):
    student, teacher, _ = advised_pair()
    activity = create_activity(client, teacher, student)
    hide(activity)

    response = timeline(client, student.user if who == "student" else teacher.user, activity)

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


@pytest.mark.parametrize("who", ["other_student", "other_teacher", "loner"])
def test_anyone_else_gets_the_same_404(client, who):
    student, teacher, _ = advised_pair()
    activity = create_activity(client, teacher, student)
    other_pair = make_advising(student=make_student(), teacher=make_teacher())
    user = {
        "other_student": other_pair.student.user,
        "other_teacher": other_pair.teacher.user,
        "loner": make_student().user,
    }[who]

    response = timeline(client, user, activity)

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


def test_an_unknown_activity_gets_the_same_404(client):
    student, _, _ = advised_pair()

    response = client.get(timeline_url(999999), headers=bearer(student.user))

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


def test_an_admin_gets_a_403_and_a_visitor_a_401(client):
    student, teacher, _ = advised_pair()
    activity = create_activity(client, teacher, student)

    assert timeline(client, make_user(UserType.ADMIN), activity).status_code == 403
    assert timeline(client, None, activity).status_code == 401


def test_the_number_of_queries_does_not_grow_with_the_events(client):
    student, teacher, _ = advised_pair()
    activity = create_activity(client, teacher, student)
    deliver(client, student, activity)
    comment(client, teacher.user, activity)
    change_status(client, student, activity, "in_progress")
    change_status(client, student, activity, "in_review")
    refuse(client, teacher, activity, files=[upload("a.png", PNG)])
    headers = bearer(student.user)

    def queries():
        with CaptureQueriesContext(connection) as captured:
            assert (
                client.get(timeline_url(activity.id), {"limit": 100}, headers=headers).status_code
                == 200
            )
        return len(captured)

    queries()  # warms up the caches
    few = queries()
    for _ in range(4):
        deliver(client, student, activity)
        comment(client, student.user, activity)
        change_status(client, student, activity, "in_progress")
        change_status(client, student, activity, "in_review")
        refuse(client, teacher, activity, files=[upload("a.png", PNG)])

    assert queries() == few


def test_the_openapi_documents_the_timeline(client):
    schema = client.get("/api/openapi.json").json()

    operation = schema["paths"]["/api/atividades/{activity_id}/linha-do-tempo"]["get"]
    assert {"before", "limit"} <= {parameter["name"] for parameter in operation["parameters"]}
    assert operation["description"]
    assert {"401", "403", "404", "422"} <= set(operation["responses"])
    components = schema["components"]["schemas"]
    for name in (
        "CreatedEventOut",
        "DeliveryEventOut",
        "StatusChangeEventOut",
        "SentForReviewEventOut",
        "CommentEventOut",
        "ReviewEventOut",
    ):
        assert name in components
