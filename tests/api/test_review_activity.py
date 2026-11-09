"""The advisor reviews an activity in review: accept completes it, refusing needs a reason (RF46)."""

import datetime
import threading
from unittest.mock import patch

import pytest
from django.db import connection, transaction
from django.test import Client
from django.utils import timezone

from apps.accounts.models import UserType
from apps.activities.models import (
    Activity,
    ActivityStatus,
    Review,
    ReviewDecision,
    Submission,
)
from apps.advising.models import Advising
from apps.advising.services import close_advising
from apps.notifications.models import Notification
from tests.api.helpers import (
    CREATE_NOTIFICATION,
    bearer,
    errors_by_field,
    inbox,
    post_json,
    send_while_the_advising_ends,
    wait_for_a_request_blocked_on_a_lock,
)
from tests.factories import make_advising, make_course, make_student, make_teacher, make_user

pytestmark = pytest.mark.django_db

NOT_FOUND = {"detail": "Atividade não encontrada."}
NOT_IN_REVIEW = {"detail": "Só uma atividade em revisão pode ser revisada."}
JUSTIFICATION_REQUIRED = "Informe a justificativa da recusa (de 1 a 500 caracteres)."
NO_JUSTIFICATION_ON_ACCEPT = "O aceite não tem justificativa."


def url(activity_id):
    return f"/api/atividades/{activity_id}/revisar"


def review(client, user, activity_id, decision, justification=None):
    body = {"decision": decision}
    if justification is not None:
        body["justification"] = justification
    return post_json(client, url(activity_id), body, headers=bearer(user))


def advised_pair():
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    return student, teacher, make_advising(student=student, teacher=teacher)


def add_activity(advising, status=ActivityStatus.IN_REVIEW, deadline=None):
    return Activity.objects.create(
        advising=advising,
        title="Capítulo 2",
        description="Entregar o capítulo 2 do TCC",
        deadline=deadline or timezone.localdate() + datetime.timedelta(days=7),
        status=status,
    )


def refresh_status(activity):
    activity.refresh_from_db()
    return activity.status


def test_the_advisor_accepts_and_the_activity_is_completed_with_the_list_item_shape(client):
    student, teacher, advising = advised_pair()
    activity = add_activity(advising)

    response = review(client, teacher.user, activity.id, ReviewDecision.ACCEPTED)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"id", "title", "deadline", "status", "overdue", "student"}
    assert body["status"] == ActivityStatus.COMPLETED
    assert body["student"] == {"id": student.user.id, "full_name": student.user.full_name}
    assert refresh_status(activity) == ActivityStatus.COMPLETED
    recorded = Review.objects.get(activity=activity)
    assert recorded.decision == ReviewDecision.ACCEPTED
    assert recorded.justification == ""


def test_the_advisor_refuses_with_a_justification_and_the_submissions_stay(client):
    _, teacher, advising = advised_pair()
    activity = add_activity(advising)
    Submission.objects.create(activity=activity)

    response = review(
        client, teacher.user, activity.id, ReviewDecision.REJECTED, "  Faltou a conclusão.  "
    )

    assert response.status_code == 200
    assert response.json()["status"] == ActivityStatus.PENDING
    assert refresh_status(activity) == ActivityStatus.PENDING
    assert Submission.objects.filter(activity=activity).count() == 1
    recorded = Review.objects.get(activity=activity)
    assert recorded.decision == ReviewDecision.REJECTED
    assert recorded.justification == "Faltou a conclusão."


def test_the_detail_shows_the_justification_to_the_student_and_the_teacher(client):
    student, teacher, advising = advised_pair()
    activity = add_activity(advising)
    review(client, teacher.user, activity.id, ReviewDecision.REJECTED, "Faltou a conclusão.")
    Activity.objects.filter(pk=activity.pk).update(status=ActivityStatus.IN_REVIEW)
    review(client, teacher.user, activity.id, ReviewDecision.ACCEPTED)

    for user in (student.user, teacher.user):
        reviews = client.get(f"/api/atividades/{activity.id}", headers=bearer(user)).json()[
            "reviews"
        ]
        assert [(r["decision"], r["justification"]) for r in reviews] == [
            ("rejected", "Faltou a conclusão."),
            ("accepted", ""),
        ]
        assert set(reviews[0]) == {"id", "decision", "justification", "decided_at"}


def test_accepting_a_late_activity_completes_it_without_the_lateness_flag(client):
    _, teacher, advising = advised_pair()
    yesterday = timezone.localdate() - datetime.timedelta(days=1)
    activity = add_activity(advising, deadline=yesterday)

    response = review(client, teacher.user, activity.id, ReviewDecision.ACCEPTED)

    assert response.status_code == 200
    assert response.json()["status"] == ActivityStatus.COMPLETED
    assert response.json()["overdue"] is False


def test_refusing_a_late_activity_keeps_it_late(client):
    _, teacher, advising = advised_pair()
    yesterday = timezone.localdate() - datetime.timedelta(days=1)
    activity = add_activity(advising, deadline=yesterday)

    response = review(client, teacher.user, activity.id, ReviewDecision.REJECTED, "Refazer.")

    assert response.json()["status"] == ActivityStatus.PENDING
    assert response.json()["overdue"] is True


@pytest.mark.parametrize("justification", [None, "", "   \n\t "])
def test_a_refusal_without_a_justification_is_rejected_and_nothing_changes(client, justification):
    _, teacher, advising = advised_pair()
    activity = add_activity(advising)

    response = review(client, teacher.user, activity.id, ReviewDecision.REJECTED, justification)

    assert response.status_code == 422
    assert errors_by_field(response) == {"justification": JUSTIFICATION_REQUIRED}
    assert refresh_status(activity) == ActivityStatus.IN_REVIEW
    assert not Review.objects.exists()
    assert not Notification.objects.exists()


def test_a_justification_of_500_characters_is_accepted_and_501_is_rejected(client):
    _, teacher, advising = advised_pair()
    activity = add_activity(advising)

    too_long = review(client, teacher.user, activity.id, "rejected", "a" * 501)
    just_right = review(client, teacher.user, activity.id, "rejected", "  " + "a" * 500 + "  ")

    assert too_long.status_code == 422
    assert "justification" in errors_by_field(too_long)
    assert just_right.status_code == 200
    assert Review.objects.get().justification == "a" * 500


def test_a_justification_on_accept_is_a_validation_error_and_nothing_changes(client):
    _, teacher, advising = advised_pair()
    activity = add_activity(advising)

    response = review(client, teacher.user, activity.id, ReviewDecision.ACCEPTED, "Bom trabalho.")

    assert response.status_code == 422
    assert errors_by_field(response) == {"justification": NO_JUSTIFICATION_ON_ACCEPT}
    assert refresh_status(activity) == ActivityStatus.IN_REVIEW
    assert not Review.objects.exists()


@pytest.mark.parametrize("body", [{}, {"decision": "archived"}, {"decision": "completed"}])
def test_an_unknown_or_missing_decision_is_a_validation_error(client, body):
    _, teacher, advising = advised_pair()
    activity = add_activity(advising)

    response = post_json(client, url(activity.id), body, headers=bearer(teacher.user))

    assert response.status_code == 422
    assert refresh_status(activity) == ActivityStatus.IN_REVIEW


def test_anonymous_gets_401_and_a_student_or_an_admin_gets_403(client):
    student, _, advising = advised_pair()
    activity = add_activity(advising)
    body = {"decision": "accepted"}

    anonymous = post_json(client, url(activity.id), body)
    by_student = post_json(client, url(activity.id), body, headers=bearer(student.user))
    by_admin = post_json(client, url(activity.id), body, headers=bearer(make_user(UserType.ADMIN)))

    assert anonymous.status_code == 401
    assert by_student.status_code == 403
    assert by_admin.status_code == 403
    assert refresh_status(activity) == ActivityStatus.IN_REVIEW


def hide_it(activity):
    Activity.objects.filter(pk=activity.pk).update(deleted_at=timezone.now())


def archive_it(activity):
    Activity.objects.filter(pk=activity.pk).update(status=ActivityStatus.ARCHIVED)


def end_the_advising(activity):
    close_advising(Advising.objects.get(pk=activity.advising_id), "Encerrada para o teste.")


@pytest.mark.parametrize("hide", [hide_it, archive_it, end_the_advising])
def test_a_deleted_archived_or_ended_activity_gets_the_same_404(client, hide):
    _, teacher, advising = advised_pair()
    activity = add_activity(advising)
    hide(activity)

    response = review(client, teacher.user, activity.id, ReviewDecision.ACCEPTED)

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    assert not Review.objects.exists()


def test_a_missing_activity_gets_the_same_404(client):
    _, teacher, _ = advised_pair()

    response = review(client, teacher.user, 999999, ReviewDecision.ACCEPTED)

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


def test_another_teacher_gets_the_same_404_and_nothing_changes(client):
    _, _, advising = advised_pair()
    activity = add_activity(advising)
    other_teacher = make_teacher(courses=[make_course()])

    response = review(client, other_teacher.user, activity.id, ReviewDecision.ACCEPTED)

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    assert refresh_status(activity) == ActivityStatus.IN_REVIEW
    assert not Review.objects.exists()


@pytest.mark.parametrize(
    "current", [ActivityStatus.PENDING, ActivityStatus.IN_PROGRESS, ActivityStatus.COMPLETED]
)
@pytest.mark.parametrize("decision", [ReviewDecision.ACCEPTED, ReviewDecision.REJECTED])
def test_an_activity_that_is_not_in_review_gets_a_409_and_nothing_changes(
    client, current, decision
):
    _, teacher, advising = advised_pair()
    activity = add_activity(advising, status=current)
    justification = "Refazer." if decision == ReviewDecision.REJECTED else None

    response = review(client, teacher.user, activity.id, decision, justification)

    assert response.status_code == 409
    assert response.json() == NOT_IN_REVIEW
    assert refresh_status(activity) == current
    assert not Review.objects.exists()
    assert not Notification.objects.exists()


def test_a_completed_activity_is_final_and_cannot_be_reviewed_again(client):
    _, teacher, advising = advised_pair()
    activity = add_activity(advising)
    assert review(client, teacher.user, activity.id, "accepted").status_code == 200

    again = review(client, teacher.user, activity.id, "rejected", "Mudei de ideia.")

    assert again.status_code == 409
    assert refresh_status(activity) == ActivityStatus.COMPLETED
    assert Review.objects.count() == 1


def test_a_refusal_notifies_the_student_once_with_the_justification(client):
    student, teacher, advising = advised_pair()
    activity = add_activity(advising)

    review(client, teacher.user, activity.id, "rejected", "Faltou a conclusão.")

    (item,) = inbox(client, student.user)
    assert item["type"] == "activity_review_result"
    assert item["reference"] == {"type": "activity", "id": activity.id}
    assert item["payload"] == {
        "activity_title": "Capítulo 2",
        "decision": "rejected",
        "justification": "Faltou a conclusão.",
        "teacher_name": teacher.user.full_name,
    }
    assert item["message"] == (
        f'{teacher.user.full_name} recusou a atividade "Capítulo 2". '
        "Justificativa: Faltou a conclusão."
    )
    assert inbox(client, teacher.user) == []
    assert Notification.objects.count() == 1


def test_an_acceptance_notifies_the_student_without_a_justification(client):
    student, teacher, advising = advised_pair()
    activity = add_activity(advising)

    review(client, teacher.user, activity.id, "accepted")

    (item,) = inbox(client, student.user)
    assert item["type"] == "activity_review_result"
    assert item["payload"] == {
        "activity_title": "Capítulo 2",
        "decision": "accepted",
        "teacher_name": teacher.user.full_name,
    }
    assert item["message"] == f'{teacher.user.full_name} aceitou a atividade "Capítulo 2".'


def test_a_failed_notification_rolls_the_whole_review_back(client):
    _, teacher, advising = advised_pair()
    activity = add_activity(advising)

    with patch(CREATE_NOTIFICATION, side_effect=RuntimeError("boom")):
        failing = Client(raise_request_exception=False)
        response = review(failing, teacher.user, activity.id, "accepted")

    assert response.status_code == 500
    assert refresh_status(activity) == ActivityStatus.IN_REVIEW
    assert not Review.objects.exists()
    assert not Notification.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_reviews_produce_only_one(client):
    _, teacher, advising = advised_pair()
    activity = add_activity(advising)
    holding_the_lock = threading.Event()
    failures = []

    def first_review_wins_while_holding_the_lock():
        try:
            with transaction.atomic():
                Advising.objects.select_for_update().get(pk=advising.pk)
                holding_the_lock.set()
                wait_for_a_request_blocked_on_a_lock()
                Activity.objects.filter(pk=activity.pk).update(status=ActivityStatus.COMPLETED)
                Review.objects.create(activity=activity, decision=ReviewDecision.ACCEPTED)
        except Exception as error:  # noqa: BLE001 - reported to the main thread
            failures.append(error)
            holding_the_lock.set()
        finally:
            connection.close()

    first = threading.Thread(target=first_review_wins_while_holding_the_lock)
    first.start()
    assert holding_the_lock.wait(timeout=10)
    second = review(client, teacher.user, activity.id, "accepted")
    first.join(timeout=10)

    assert failures == []
    assert second.status_code == 409
    assert second.json() == NOT_IN_REVIEW
    assert Review.objects.filter(activity=activity).count() == 1
    assert not Notification.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_if_the_advising_ends_during_the_action_it_gets_404_and_nothing_changes(client):
    _, teacher, advising = advised_pair()
    activity = add_activity(advising)

    response = send_while_the_advising_ends(
        advising, lambda: review(client, teacher.user, activity.id, "accepted")
    )

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    assert not Review.objects.exists()
    assert not Notification.objects.filter(type="activity_review_result").exists()
    assert refresh_status(activity) == ActivityStatus.ARCHIVED
