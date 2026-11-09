import datetime
import threading

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import UserType
from apps.activities.models import Activity, ActivityStatus, Review, ReviewDecision, Submission
from apps.advising.models import Advising, AdvisingStatus
from apps.advising.services import close_advising
from tests.api.helpers import (
    bearer,
    errors_by_field,
    send_while_the_advising_ends,
    wait_for_a_request_blocked_on_a_lock,
)
from tests.api.uploads import PDF, upload
from tests.factories import make_advising, make_course, make_student, make_teacher, make_user

pytestmark = pytest.mark.django_db


def url(activity_id):
    return f"/api/atividades/{activity_id}/status"


def change(client, user, activity_id, status):
    return client.patch(
        url(activity_id),
        data={"status": status},
        content_type="application/json",
        headers=bearer(user),
    )


def advised_pair():
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    return student, teacher, make_advising(student=student, teacher=teacher)


def add_activity(advising, status=ActivityStatus.PENDING, deadline=None):
    return Activity.objects.create(
        advising=advising,
        title="Capítulo 2",
        description="Entregar o capítulo 2 do TCC",
        deadline=deadline or timezone.localdate() + datetime.timedelta(days=7),
        status=status,
    )


@pytest.mark.parametrize(
    ("current", "requested"),
    [
        (ActivityStatus.PENDING, ActivityStatus.IN_PROGRESS),
        (ActivityStatus.IN_PROGRESS, ActivityStatus.PENDING),
    ],
)
def test_the_student_moves_between_pending_and_in_progress_and_gets_the_list_item(
    client, current, requested
):
    student, _, advising = advised_pair()
    activity = add_activity(advising, status=current)

    response = change(client, student.user, activity.id, requested)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"id", "title", "deadline", "status", "overdue", "student"}
    assert body["id"] == activity.id
    assert body["title"] == "Capítulo 2"
    assert body["status"] == requested
    assert body["overdue"] is False
    assert body["student"] == {"id": student.user.id, "full_name": student.user.full_name}
    activity.refresh_from_db()
    assert activity.status == requested


def test_the_student_moves_back_and_forth_as_many_times_as_wanted(client):
    student, _, advising = advised_pair()
    activity = add_activity(advising)

    statuses = [
        ActivityStatus.IN_PROGRESS,
        ActivityStatus.PENDING,
        ActivityStatus.IN_PROGRESS,
        ActivityStatus.PENDING,
    ]
    for status in statuses:
        assert change(client, student.user, activity.id, status).json()["status"] == status

    activity.refresh_from_db()
    assert activity.status == ActivityStatus.PENDING


@pytest.mark.parametrize("current", [ActivityStatus.PENDING, ActivityStatus.IN_PROGRESS])
def test_a_late_activity_can_still_move_and_is_reported_as_overdue(client, current):
    student, _, advising = advised_pair()
    yesterday = timezone.localdate() - datetime.timedelta(days=1)
    activity = add_activity(advising, status=current, deadline=yesterday)
    other = (
        ActivityStatus.IN_PROGRESS if current == ActivityStatus.PENDING else ActivityStatus.PENDING
    )

    response = change(client, student.user, activity.id, other)

    assert response.status_code == 200
    assert response.json()["status"] == other
    assert response.json()["overdue"] is True


@pytest.mark.parametrize("current", [ActivityStatus.PENDING, ActivityStatus.IN_PROGRESS])
def test_asking_for_the_current_status_answers_200_and_writes_nothing(client, current):
    student, _, advising = advised_pair()
    activity = add_activity(advising, status=current)

    with CaptureQueriesContext(connection) as queries:
        response = change(client, student.user, activity.id, current)

    assert response.status_code == 200
    assert response.json()["status"] == current
    assert not [q for q in queries if q["sql"].lstrip().upper().startswith("UPDATE")]
    activity.refresh_from_db()
    assert activity.status == current


NO_SUBMISSION = "Envie ao menos uma entrega antes de enviar a atividade para revisão."
IN_REVIEW = "Esta atividade está em revisão e só o orientador pode alterar o status."
COMPLETED = "Esta atividade já foi concluída e não pode ser alterada."


def deliver(client, student, activity):
    """A delivery through the real route of the delivery (#49)."""
    response = client.post(
        f"/api/atividades/{activity.id}/entregas",
        data={"files": [upload("relatorio.pdf", PDF)]},
        headers=bearer(student.user),
    )
    assert response.status_code == 201


@pytest.mark.parametrize("current", [ActivityStatus.PENDING, ActivityStatus.IN_PROGRESS])
def test_the_student_sends_an_activity_with_a_delivery_for_review(client, current):
    student, _, advising = advised_pair()
    activity = add_activity(advising, status=current)
    deliver(client, student, activity)

    response = change(client, student.user, activity.id, ActivityStatus.IN_REVIEW)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"id", "title", "deadline", "status", "overdue", "student"}
    assert body["status"] == ActivityStatus.IN_REVIEW
    activity.refresh_from_db()
    assert activity.status == ActivityStatus.IN_REVIEW


@pytest.mark.parametrize("current", [ActivityStatus.PENDING, ActivityStatus.IN_PROGRESS])
def test_sending_for_review_without_any_delivery_gets_a_409_and_nothing_changes(client, current):
    student, _, advising = advised_pair()
    activity = add_activity(advising, status=current)

    response = change(client, student.user, activity.id, ActivityStatus.IN_REVIEW)

    assert response.status_code == 409
    assert response.json() == {"detail": NO_SUBMISSION}
    activity.refresh_from_db()
    assert activity.status == current


def test_a_delivery_of_another_activity_does_not_count(client):
    student, _, advising = advised_pair()
    activity = add_activity(advising)
    other = add_activity(advising)
    deliver(client, student, other)

    response = change(client, student.user, activity.id, ActivityStatus.IN_REVIEW)

    assert response.status_code == 409
    assert response.json() == {"detail": NO_SUBMISSION}


def test_after_a_refusal_the_student_sends_it_again_without_a_new_delivery(client):
    student, _, advising = advised_pair()
    activity = add_activity(advising)
    deliver(client, student, activity)
    assert change(client, student.user, activity.id, ActivityStatus.IN_REVIEW).status_code == 200
    # A refusal by the advisor (#61): it records the review and sends the activity back to pending.
    Review.objects.create(activity=activity, decision=ReviewDecision.REJECTED)
    Activity.objects.filter(pk=activity.pk).update(status=ActivityStatus.PENDING)

    response = change(client, student.user, activity.id, ActivityStatus.IN_REVIEW)

    assert response.status_code == 200
    assert response.json()["status"] == ActivityStatus.IN_REVIEW
    assert Submission.objects.filter(activity=activity).count() == 1


@pytest.mark.parametrize(
    "requested",
    [ActivityStatus.PENDING, ActivityStatus.IN_PROGRESS, ActivityStatus.IN_REVIEW],
)
@pytest.mark.parametrize(
    ("current", "message"),
    [(ActivityStatus.IN_REVIEW, IN_REVIEW), (ActivityStatus.COMPLETED, COMPLETED)],
)
def test_an_activity_in_review_or_completed_gets_a_409_whatever_is_asked(
    client, current, message, requested
):
    student, _, advising = advised_pair()
    activity = add_activity(advising, status=current)
    this_activity = Activity.objects.filter(pk=activity.pk)
    # A delivery needs a pending activity (RN16): deliver first, then put it in the wanted status.
    this_activity.update(status=ActivityStatus.PENDING)
    deliver(client, student, activity)
    this_activity.update(status=current)

    response = change(client, student.user, activity.id, requested)

    assert response.status_code == 409
    assert response.json() == {"detail": message}
    activity.refresh_from_db()
    assert activity.status == current


NOT_FOUND = {"detail": "Atividade não encontrada."}


def hide_it(activity):
    Activity.objects.filter(pk=activity.pk).update(deleted_at=timezone.now())


def archive_it(activity):
    Activity.objects.filter(pk=activity.pk).update(status=ActivityStatus.ARCHIVED)


def end_the_advising(activity):
    close_advising(Advising.objects.get(pk=activity.advising_id), "Encerrada para o teste.")


@pytest.mark.parametrize("hide", [hide_it, archive_it, end_the_advising])
def test_a_deleted_archived_or_ended_activity_gets_the_same_404(client, hide):
    student, _, advising = advised_pair()
    activity = add_activity(advising)
    hide(activity)

    response = change(client, student.user, activity.id, ActivityStatus.IN_PROGRESS)

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


def test_a_missing_activity_gets_the_same_404(client):
    student, _, _ = advised_pair()

    response = change(client, student.user, 999999, ActivityStatus.IN_PROGRESS)

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


@pytest.mark.parametrize("who", ["other", "loner"])
def test_the_activity_of_another_student_gets_the_same_404_and_nothing_changes(client, who):
    _, _, advising = advised_pair()
    activity = add_activity(advising)
    other = (
        make_advising(student=make_student(), teacher=make_teacher()).student
        if who == "other"
        else make_student()
    )

    response = change(client, other.user, activity.id, ActivityStatus.IN_PROGRESS)

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    activity.refresh_from_db()
    assert activity.status == ActivityStatus.PENDING


@pytest.mark.parametrize(
    "body",
    [
        {"status": "completed"},
        {"status": "archived"},
        {"status": "qualquer"},
        {"status": None},
        {},
    ],
)
def test_a_status_the_student_cannot_ask_for_is_a_validation_error(client, body):
    student, _, advising = advised_pair()
    activity = add_activity(advising)

    response = client.patch(
        url(activity.id), data=body, content_type="application/json", headers=bearer(student.user)
    )

    assert response.status_code == 422
    assert "status" in errors_by_field(response)
    activity.refresh_from_db()
    assert activity.status == ActivityStatus.PENDING


@pytest.mark.parametrize("who", ["other", "in_review", "missing"])
def test_the_body_is_validated_before_the_activity_is_looked_up(client, who):
    student, _, advising = advised_pair()
    activity = add_activity(
        advising,
        status=ActivityStatus.IN_REVIEW if who == "in_review" else ActivityStatus.PENDING,
    )
    user = (
        make_advising(student=make_student(), teacher=make_teacher()).student.user
        if who == "other"
        else student.user
    )

    response = change(client, user, 999999 if who == "missing" else activity.id, "completed")

    assert response.status_code == 422


@pytest.mark.parametrize("who", ["advisor", "admin"])
def test_a_teacher_or_an_admin_gets_a_403(client, who):
    _, teacher, advising = advised_pair()
    activity = add_activity(advising)
    user = teacher.user if who == "advisor" else make_user(UserType.ADMIN)

    response = change(client, user, activity.id, ActivityStatus.IN_PROGRESS)

    assert response.status_code == 403
    activity.refresh_from_db()
    assert activity.status == ActivityStatus.PENDING


def test_an_anonymous_request_gets_a_401(client):
    _, _, advising = advised_pair()
    activity = add_activity(advising)

    response = client.patch(
        url(activity.id),
        data={"status": "in_progress"},
        content_type="application/json",
    )

    assert response.status_code == 401


@pytest.mark.django_db(transaction=True)
def test_an_advising_that_ends_during_the_action_gets_the_404_and_nothing_changes(client):
    student, _, advising = advised_pair()
    activity = add_activity(advising)

    response = send_while_the_advising_ends(
        advising,
        lambda: change(client, student.user, activity.id, ActivityStatus.IN_PROGRESS),
    )

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    activity.refresh_from_db()
    assert activity.status == ActivityStatus.ARCHIVED
    assert Advising.objects.get(pk=advising.pk).status == AdvisingStatus.ENDED


def send_while_a_delivery_commits(activity, send_request):
    """Sends a request that must wait for the activity lock while a delivery commits.

    A second transaction holds the activity row; once a request is blocked on it, that
    transaction records a submission and commits, so the request runs after the delivery.
    Needs a test marked `django_db(transaction=True)`.
    """
    holding_the_lock = threading.Event()
    failures = []

    def deliver_while_holding_the_lock():
        try:
            with transaction.atomic():
                Activity.objects.select_for_update().get(pk=activity.pk)
                holding_the_lock.set()
                wait_for_a_request_blocked_on_a_lock()
                Submission.objects.create(activity=activity)
        except Exception as error:  # noqa: BLE001 - reported to the main thread
            failures.append(error)
            holding_the_lock.set()
        finally:
            connection.close()

    delivering = threading.Thread(target=deliver_while_holding_the_lock)
    delivering.start()
    assert holding_the_lock.wait(timeout=10)
    response = send_request()
    delivering.join(timeout=10)
    assert failures == []
    return response


@pytest.mark.django_db(transaction=True)
def test_a_delivery_that_commits_while_the_request_waits_is_seen_by_the_request(client):
    student, _, advising = advised_pair()
    activity = add_activity(advising)

    response = send_while_a_delivery_commits(
        activity,
        lambda: change(client, student.user, activity.id, ActivityStatus.IN_REVIEW),
    )

    assert response.status_code == 200
    activity.refresh_from_db()
    assert activity.status == ActivityStatus.IN_REVIEW


def count_queries(client, user, activity_id):
    with CaptureQueriesContext(connection) as queries:
        response = change(client, user, activity_id, ActivityStatus.IN_REVIEW)
    assert response.status_code == 200
    return len(queries)


def test_the_number_of_queries_does_not_depend_on_the_number_of_submissions(client):
    student, _, advising = advised_pair()
    warm_up = add_activity(advising)
    Submission.objects.create(activity=warm_up)
    count_queries(client, student.user, warm_up.id)  # warms up the caches
    one = add_activity(advising)
    Submission.objects.create(activity=one)
    five = add_activity(advising)
    Submission.objects.bulk_create([Submission(activity=five) for _ in range(5)])

    assert count_queries(client, student.user, five.id) == count_queries(
        client, student.user, one.id
    )


def test_the_openapi_documents_the_route_the_accepted_values_and_the_errors(client):
    schema = client.get("/api/openapi.json").json()
    operation = schema["paths"]["/api/atividades/{activity_id}/status"]["patch"]

    assert {"200", "401", "403", "404", "409", "422"} <= set(operation["responses"])
    body = schema["components"]["schemas"]["ActivityStatusIn"]
    assert body["required"] == ["status"]
    accepted = schema["components"]["schemas"]["StudentStatus"]
    assert body["properties"]["status"] == {"$ref": "#/components/schemas/StudentStatus"}
    assert set(accepted["enum"]) == {"pending", "in_progress", "in_review"}
    for text in ("pending e in_progress", "in_review", "409", "404"):
        assert text in operation["description"]
