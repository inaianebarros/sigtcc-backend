"""The notifications of a delivery, a comment and a submission for review (RF36, RF42, RF43, RF47)."""

from unittest.mock import patch

import pytest
from django.core.files.storage import InMemoryStorage, storages
from django.test import Client

from apps.activities.models import (
    Activity,
    ActivityStatus,
    Comment,
    Review,
    ReviewDecision,
    Submission,
)
from apps.advising.models import Advising
from apps.advising.services import close_advising
from apps.notifications.models import Notification
from tests.api.helpers import CREATE_NOTIFICATION, bearer, inbox, post_json
from tests.api.uploads import PDF, upload
from tests.factories import make_advising, make_course, make_student, make_teacher

pytestmark = pytest.mark.django_db


def advised_pair():
    course = make_course()
    teacher = make_teacher(courses=[course], full_name="Carlos Lima")
    student = make_student(course=course, full_name="Ana Souza")
    return student, teacher, make_advising(student=student, teacher=teacher)


def add_activity(advising, status=ActivityStatus.PENDING):
    return Activity.objects.create(
        advising=advising,
        title="Capítulo 2",
        description="Entregar o capítulo 2 do TCC",
        deadline="2099-12-31",
        status=status,
    )


def deliver(client, user, activity_id, files=None):
    files = files or [upload("relatorio.pdf", PDF)]
    return client.post(
        f"/api/atividades/{activity_id}/entregas", data={"files": files}, headers=bearer(user)
    )


def comment(client, user, activity_id, text="Uma observação."):
    return post_json(
        client, f"/api/atividades/{activity_id}/comentarios", {"text": text}, headers=bearer(user)
    )


def change_status(client, user, activity_id, status):
    return client.patch(
        f"/api/atividades/{activity_id}/status",
        data={"status": status},
        content_type="application/json",
        headers=bearer(user),
    )


def stored_keys():
    storage = storages["submissions"]
    try:
        directories, _ = storage.listdir("entregas")
    except FileNotFoundError:
        return []
    keys = []
    for directory in directories:
        _, names = storage.listdir(f"entregas/{directory}")
        keys += [f"entregas/{directory}/{name}" for name in names]
    return keys


def with_a_submission(activity):
    return Submission.objects.create(activity=activity)


def only(client, user):
    items = inbox(client, user)
    assert len(items) == 1
    return items[0]


class TestDelivery:
    def test_a_delivery_notifies_the_advisor_exactly_once(self, client):
        student, teacher, advising = advised_pair()
        activity = add_activity(advising)
        files = [upload(f"{index}.pdf", PDF + bytes([index])) for index in range(3)]

        response = deliver(client, student.user, activity.id, files)

        assert response.status_code == 201
        item = only(client, teacher.user)
        assert item["type"] == "activity_submitted"
        assert item["reference"] == {"type": "activity", "id": activity.id}
        assert item["is_read"] is False
        assert item["payload"] == {"activity_title": "Capítulo 2", "student_name": "Ana Souza"}
        assert item["message"] == 'Ana Souza entregou arquivos da atividade "Capítulo 2".'
        assert inbox(client, student.user) == []
        assert Notification.objects.count() == 1

    def test_each_delivery_notifies_again(self, client):
        student, teacher, advising = advised_pair()
        activity = add_activity(advising)

        deliver(client, student.user, activity.id)
        deliver(client, student.user, activity.id)

        assert len(inbox(client, teacher.user)) == 2

    def test_a_delivery_that_is_refused_leaves_no_notification(self, client):
        student, _, advising = advised_pair()
        in_review = add_activity(advising, ActivityStatus.IN_REVIEW)
        completed = add_activity(advising, ActivityStatus.COMPLETED)
        other_student = make_student(course=student.course)

        assert deliver(client, student.user, in_review.id).status_code == 409
        assert deliver(client, student.user, completed.id).status_code == 409
        assert deliver(client, other_student.user, completed.id).status_code == 404
        assert deliver(client, student.user, 999999).status_code == 404
        assert (
            deliver(client, student.user, in_review.id, [upload("x.exe", b"MZ")]).status_code == 422
        )
        assert not Notification.objects.exists()

    def test_a_storage_failure_leaves_no_notification(self, client):
        student, _, advising = advised_pair()
        activity = add_activity(advising)

        with patch.object(InMemoryStorage, "save", side_effect=OSError("caiu")):
            response = deliver(client, student.user, activity.id)

        assert response.status_code == 503
        assert not Notification.objects.exists()

    def test_a_failing_notification_rolls_the_delivery_back(self, client):
        student, _, advising = advised_pair()
        activity = add_activity(advising)
        quiet_client = Client(raise_request_exception=False)

        with patch(CREATE_NOTIFICATION, side_effect=RuntimeError("boom")):
            response = deliver(quiet_client, student.user, activity.id)

        assert response.status_code == 500
        assert not Submission.objects.exists()
        assert not Notification.objects.exists()

    def test_a_failing_notification_also_removes_the_files_already_stored(self, client):
        student, _, advising = advised_pair()
        activity = add_activity(advising)
        quiet_client = Client(raise_request_exception=False)

        with patch(CREATE_NOTIFICATION, side_effect=RuntimeError("boom")):
            deliver(quiet_client, student.user, activity.id)

        assert stored_keys() == []


class TestComment:
    def test_the_teacher_comment_notifies_the_student_only(self, client):
        student, teacher, advising = advised_pair()
        activity = add_activity(advising)

        response = comment(client, teacher.user, activity.id, "Revise a introdução.")

        assert response.status_code == 201
        item = only(client, student.user)
        assert item["type"] == "activity_commented"
        assert item["reference"] == {"type": "activity", "id": activity.id}
        assert item["payload"] == {"activity_title": "Capítulo 2", "author_name": "Carlos Lima"}
        assert item["message"] == 'Carlos Lima comentou na atividade "Capítulo 2".'
        assert inbox(client, teacher.user) == []
        assert Notification.objects.count() == 1

    def test_the_student_comment_notifies_the_teacher_only(self, client):
        student, teacher, advising = advised_pair()
        activity = add_activity(advising)

        response = comment(client, student.user, activity.id, "Dúvida sobre o capítulo.")

        assert response.status_code == 201
        item = only(client, teacher.user)
        assert item["type"] == "activity_commented"
        assert item["payload"] == {"activity_title": "Capítulo 2", "author_name": "Ana Souza"}
        assert item["message"] == 'Ana Souza comentou na atividade "Capítulo 2".'
        assert inbox(client, student.user) == []

    def test_the_actor_never_notifies_themselves_across_a_conversation(self, client):
        student, teacher, advising = advised_pair()
        activity = add_activity(advising)

        comment(client, student.user, activity.id, "um")
        comment(client, teacher.user, activity.id, "dois")
        comment(client, student.user, activity.id, "três")

        assert len(inbox(client, teacher.user)) == 2
        assert len(inbox(client, student.user)) == 1

    @pytest.mark.parametrize("status", list(ActivityStatus)[:3])
    def test_it_notifies_in_every_open_status(self, client, status):
        student, teacher, advising = advised_pair()
        activity = add_activity(advising, status)

        comment(client, student.user, activity.id)

        assert len(inbox(client, teacher.user)) == 1

    def test_a_comment_that_is_refused_leaves_no_notification(self, client):
        student, teacher, advising = advised_pair()
        completed = add_activity(advising, ActivityStatus.COMPLETED)
        open_activity = add_activity(advising)
        outsider = make_student(course=student.course)

        assert comment(client, student.user, completed.id).status_code == 409
        assert comment(client, outsider.user, open_activity.id).status_code == 404
        assert comment(client, teacher.user, 999999).status_code == 404
        assert comment(client, teacher.user, open_activity.id, "   ").status_code == 422
        assert comment(client, teacher.user, open_activity.id, "a" * 1001).status_code == 422
        assert not Notification.objects.exists()
        assert not Comment.objects.exists()

    def test_after_the_advising_ends_a_comment_leaves_no_notification(self, client):
        student, teacher, advising = advised_pair()
        activity = add_activity(advising)
        close_advising(Advising.objects.get(pk=advising.pk), "Encerrada para o teste.")

        assert comment(client, student.user, activity.id).status_code == 404
        assert comment(client, teacher.user, activity.id).status_code == 404
        assert not Notification.objects.filter(type="activity_commented").exists()

    def test_a_failing_notification_rolls_the_comment_back(self, client):
        student, _, advising = advised_pair()
        activity = add_activity(advising)
        quiet_client = Client(raise_request_exception=False)

        with patch(CREATE_NOTIFICATION, side_effect=RuntimeError("boom")):
            response = comment(quiet_client, student.user, activity.id)

        assert response.status_code == 500
        assert not Comment.objects.exists()
        assert not Notification.objects.exists()


class TestSendForReview:
    def test_sending_for_review_notifies_the_advisor(self, client):
        student, teacher, advising = advised_pair()
        activity = add_activity(advising, ActivityStatus.IN_PROGRESS)
        with_a_submission(activity)

        response = change_status(client, student.user, activity.id, ActivityStatus.IN_REVIEW)

        assert response.status_code == 200
        item = only(client, teacher.user)
        assert item["type"] == "activity_awaiting_review"
        assert item["reference"] == {"type": "activity", "id": activity.id}
        assert item["payload"] == {"activity_title": "Capítulo 2", "student_name": "Ana Souza"}
        assert item["message"] == 'Ana Souza enviou a atividade "Capítulo 2" para revisão.'
        assert inbox(client, student.user) == []
        assert Notification.objects.count() == 1

    def test_a_resend_after_a_refusal_notifies_again(self, client):
        student, teacher, advising = advised_pair()
        activity = add_activity(advising)
        with_a_submission(activity)
        change_status(client, student.user, activity.id, ActivityStatus.IN_REVIEW)
        Review.objects.create(
            activity=activity, decision=ReviewDecision.REJECTED, justification="Refazer."
        )
        Activity.objects.filter(pk=activity.pk).update(status=ActivityStatus.PENDING)

        response = change_status(client, student.user, activity.id, ActivityStatus.IN_REVIEW)

        assert response.status_code == 200
        types = [item["type"] for item in inbox(client, teacher.user)]
        assert types == ["activity_awaiting_review", "activity_awaiting_review"]

    @pytest.mark.parametrize(
        ("current", "requested"),
        [
            (ActivityStatus.PENDING, ActivityStatus.IN_PROGRESS),
            (ActivityStatus.IN_PROGRESS, ActivityStatus.PENDING),
            (ActivityStatus.PENDING, ActivityStatus.PENDING),
            (ActivityStatus.IN_PROGRESS, ActivityStatus.IN_PROGRESS),
        ],
    )
    def test_moving_between_pending_and_in_progress_does_not_notify(
        self, client, current, requested
    ):
        student, _, advising = advised_pair()
        activity = add_activity(advising, current)
        with_a_submission(activity)

        response = change_status(client, student.user, activity.id, requested)

        assert response.status_code == 200
        assert not Notification.objects.exists()

    def test_a_refused_change_leaves_no_notification(self, client):
        student, _, advising = advised_pair()
        no_delivery = add_activity(advising)
        in_review = add_activity(advising, ActivityStatus.IN_REVIEW)
        completed = add_activity(advising, ActivityStatus.COMPLETED)
        outsider = make_student(course=student.course)

        assert change_status(client, student.user, no_delivery.id, "in_review").status_code == 409
        assert change_status(client, student.user, in_review.id, "in_review").status_code == 409
        assert change_status(client, student.user, completed.id, "in_review").status_code == 409
        assert change_status(client, outsider.user, no_delivery.id, "in_review").status_code == 404
        assert change_status(client, student.user, no_delivery.id, "completed").status_code == 422
        assert not Notification.objects.exists()

    def test_a_failing_notification_rolls_the_status_back(self, client):
        student, _, advising = advised_pair()
        activity = add_activity(advising)
        with_a_submission(activity)
        quiet_client = Client(raise_request_exception=False)

        with patch(CREATE_NOTIFICATION, side_effect=RuntimeError("boom")):
            response = change_status(
                quiet_client, student.user, activity.id, ActivityStatus.IN_REVIEW
            )

        assert response.status_code == 500
        activity.refresh_from_db()
        assert activity.status == ActivityStatus.PENDING
        assert not Notification.objects.exists()


def test_the_three_types_list_in_the_recipient_inbox_newest_first(client):
    student, teacher, advising = advised_pair()
    activity = add_activity(advising)

    deliver(client, student.user, activity.id)
    comment(client, student.user, activity.id)
    change_status(client, student.user, activity.id, ActivityStatus.IN_REVIEW)

    assert [item["type"] for item in inbox(client, teacher.user)] == [
        "activity_awaiting_review",
        "activity_commented",
        "activity_submitted",
    ]
    assert all(item["message"] and item["payload"] for item in inbox(client, teacher.user))
