"""A decision stands only for the latest delivery the advisor had in sight (RF46, RN21)."""

import pytest
from django.core.files.storage import storages

from apps.activities.models import ActivityStatus, Review, ReviewDecision, ReviewFile
from apps.notifications.models import Notification
from tests.api.activity_flow import (
    BASE,
    accept,
    advised_pair,
    change_status,
    create_activity,
    deliver,
    latest_submission_id,
    refuse,
    refuse_without_files,
)
from tests.api.helpers import bearer, errors_by_field, post_json
from tests.api.uploads import PDF, PNG, upload

pytestmark = pytest.mark.django_db

NEW_DELIVERY = {
    "detail": "A atividade recebeu uma nova entrega. Confira a linha do tempo antes de decidir."
}


def activity_in_review(client):
    student, teacher, _ = advised_pair()
    activity = create_activity(client, teacher, student)
    assert deliver(client, student, activity).status_code == 201
    assert change_status(client, student, activity, "in_review").status_code == 200
    return student, teacher, activity


def status_of(activity):
    activity.refresh_from_db()
    return activity.status


def refusal_attachments_in_storage(activity):
    folder = f"revisoes/{activity.id}"
    storage = storages["submissions"]
    return storage.listdir(folder)[1] if storage.exists(folder) else []


def review_result_notifications():
    return Notification.objects.filter(type="activity_review_result")


def test_accepting_the_latest_delivery_completes_the_activity(client):
    _, teacher, activity = activity_in_review(client)

    response = accept(client, teacher, activity, latest_submission_id(activity))

    assert response.status_code == 200
    assert status_of(activity) == ActivityStatus.COMPLETED
    assert Review.objects.get(activity=activity).decision == ReviewDecision.ACCEPTED


def test_refusing_the_latest_delivery_without_files_sends_the_activity_back_to_pending(client):
    _, teacher, activity = activity_in_review(client)

    response = refuse_without_files(client, teacher, activity, latest_submission_id(activity))

    assert response.status_code == 200
    assert status_of(activity) == ActivityStatus.PENDING
    assert Review.objects.get(activity=activity).decision == ReviewDecision.REJECTED


def test_accepting_an_older_delivery_after_a_new_one_is_rejected_and_nothing_changes(client):
    student, teacher, activity = activity_in_review(client)
    seen_by_the_first_tab = latest_submission_id(activity)
    assert refuse_without_files(client, teacher, activity).status_code == 200
    assert deliver(client, student, activity).status_code == 201
    assert change_status(client, student, activity, "in_review").status_code == 200
    notifications_before = review_result_notifications().count()

    response = accept(client, teacher, activity, seen_by_the_first_tab)

    assert response.status_code == 409
    assert response.json() == NEW_DELIVERY
    assert status_of(activity) == ActivityStatus.IN_REVIEW
    assert Review.objects.filter(activity=activity).count() == 1
    assert review_result_notifications().count() == notifications_before


def test_a_delivery_of_another_activity_or_a_missing_one_gets_the_same_409(client):
    _, teacher, activity = activity_in_review(client)
    _, _, other_activity = activity_in_review(client)

    from_another_activity = accept(client, teacher, activity, latest_submission_id(other_activity))
    missing = accept(client, teacher, activity, 999999)

    assert (from_another_activity.status_code, missing.status_code) == (409, 409)
    assert from_another_activity.json() == missing.json() == NEW_DELIVERY
    assert status_of(activity) == ActivityStatus.IN_REVIEW
    assert not Review.objects.exists()
    assert not review_result_notifications().exists()


def test_a_decision_without_the_delivery_id_is_a_validation_error(client):
    _, teacher, activity = activity_in_review(client)

    response = post_json(
        client,
        f"{BASE}/{activity.id}/revisar",
        {"decision": "accepted"},
        headers=bearer(teacher.user),
    )

    assert response.status_code == 422
    assert "submission_id" in errors_by_field(response)
    assert status_of(activity) == ActivityStatus.IN_REVIEW
    assert not Review.objects.exists()


def test_refusing_the_latest_delivery_with_files_sends_it_back_to_pending_and_keeps_them(client):
    _, teacher, activity = activity_in_review(client)

    response = refuse(
        client,
        teacher,
        activity,
        files=[upload("notas.pdf", PDF), upload("foto.png", PNG)],
        submission_id=latest_submission_id(activity),
    )

    assert response.status_code == 200
    assert status_of(activity) == ActivityStatus.PENDING
    review = Review.objects.get(activity=activity)
    assert review.decision == ReviewDecision.REJECTED
    assert review.files.count() == 2
    assert len(refusal_attachments_in_storage(activity)) == 2


def test_refusing_an_older_delivery_with_files_is_rejected_and_stores_no_file(client):
    student, teacher, activity = activity_in_review(client)
    seen_by_the_first_tab = latest_submission_id(activity)
    assert refuse_without_files(client, teacher, activity).status_code == 200
    assert deliver(client, student, activity).status_code == 201
    assert change_status(client, student, activity, "in_review").status_code == 200
    notifications_before = review_result_notifications().count()

    response = refuse(
        client, teacher, activity, files=[upload("a.pdf", PDF)], submission_id=seen_by_the_first_tab
    )

    assert response.status_code == 409
    assert response.json() == NEW_DELIVERY
    assert status_of(activity) == ActivityStatus.IN_REVIEW
    assert Review.objects.filter(activity=activity).count() == 1
    assert not ReviewFile.objects.exists()
    assert refusal_attachments_in_storage(activity) == []
    assert review_result_notifications().count() == notifications_before


def test_refusing_with_files_and_a_delivery_of_another_activity_gets_the_same_409(client):
    _, teacher, activity = activity_in_review(client)
    _, _, other_activity = activity_in_review(client)

    response = refuse(
        client,
        teacher,
        activity,
        files=[upload("a.pdf", PDF)],
        submission_id=latest_submission_id(other_activity),
    )

    assert response.status_code == 409
    assert response.json() == NEW_DELIVERY
    assert status_of(activity) == ActivityStatus.IN_REVIEW
    assert not Review.objects.exists()
    assert not ReviewFile.objects.exists()
    assert refusal_attachments_in_storage(activity) == []
    assert not review_result_notifications().exists()


def test_a_refusal_with_files_without_the_delivery_id_is_a_validation_error(client):
    _, teacher, activity = activity_in_review(client)

    response = client.post(
        f"{BASE}/{activity.id}/recusar",
        data={"justification": "Refazer.", "files": [upload("a.pdf", PDF)]},
        headers=bearer(teacher.user),
    )

    assert response.status_code == 422
    assert "submission_id" in errors_by_field(response)
    assert status_of(activity) == ActivityStatus.IN_REVIEW
    assert not Review.objects.exists()
    assert refusal_attachments_in_storage(activity) == []
