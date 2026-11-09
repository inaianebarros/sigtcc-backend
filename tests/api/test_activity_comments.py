"""The student of an activity and their active advisor comment on it and read the comments (RF32)."""

import datetime
import threading

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import UserType
from apps.activities.models import Activity, ActivityStatus, Comment
from apps.advising.models import Advising
from apps.advising.services import close_advising
from apps.notifications.models import Notification
from tests.api.helpers import (
    bearer,
    errors_by_field,
    post_json,
    send_while_the_advising_ends,
    wait_for_a_request_blocked_on_a_lock,
)
from tests.factories import make_advising, make_course, make_student, make_teacher, make_user

pytestmark = pytest.mark.django_db

NOT_FOUND = {"detail": "Atividade não encontrada."}
COMPLETED = {"detail": "Esta atividade já foi concluída e não aceita novos comentários."}


def url(activity_id):
    return f"/api/atividades/{activity_id}/comentarios"


def comment(client, user, activity_id, text):
    return post_json(client, url(activity_id), {"text": text}, headers=bearer(user))


def read(client, user, activity_id, **params):
    return client.get(url(activity_id), params, headers=bearer(user))


def advised_pair():
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    return student, teacher, make_advising(student=student, teacher=teacher)


def add_activity(advising, status=ActivityStatus.PENDING):
    return Activity.objects.create(
        advising=advising,
        title="Capítulo 2",
        description="Entregar o capítulo 2 do TCC",
        deadline=timezone.localdate() + datetime.timedelta(days=7),
        status=status,
    )


@pytest.mark.parametrize(
    "status",
    [ActivityStatus.PENDING, ActivityStatus.IN_PROGRESS, ActivityStatus.IN_REVIEW],
)
def test_the_student_and_the_advisor_comment_while_the_activity_is_open(client, status):
    student, teacher, advising = advised_pair()
    activity = add_activity(advising, status=status)

    by_student = comment(client, student.user, activity.id, "Dúvida sobre o capítulo.")
    by_teacher = comment(client, teacher.user, activity.id, "Pode enviar até sexta.")

    assert by_student.status_code == 201
    assert by_teacher.status_code == 201
    body = by_student.json()
    assert set(body) == {"id", "text", "created_at", "author"}
    assert body["text"] == "Dúvida sobre o capítulo."
    assert body["author"] == {
        "id": student.user.id,
        "full_name": student.user.full_name,
        "type": "student",
    }
    assert by_teacher.json()["author"]["type"] == "teacher"
    assert Comment.objects.filter(activity=activity).count() == 2


def test_the_text_is_trimmed_and_line_breaks_and_html_are_kept_as_plain_text(client):
    student, _, advising = advised_pair()
    activity = add_activity(advising)

    response = comment(client, student.user, activity.id, "  Linha 1\n\nLinha <b>2</b> @prof  ")

    assert response.status_code == 201
    assert response.json()["text"] == "Linha 1\n\nLinha <b>2</b> @prof"
    assert Comment.objects.get().text == "Linha 1\n\nLinha <b>2</b> @prof"


@pytest.mark.parametrize("text", ["", "   ", "\n\t \n"])
def test_an_empty_or_whitespace_only_text_is_a_validation_error(client, text):
    student, _, advising = advised_pair()
    activity = add_activity(advising)

    response = comment(client, student.user, activity.id, text)

    assert response.status_code == 422
    assert "text" in errors_by_field(response)
    assert not Comment.objects.exists()


def test_a_text_of_1000_characters_is_accepted_and_1001_is_rejected(client):
    student, _, advising = advised_pair()
    activity = add_activity(advising)

    too_long = comment(client, student.user, activity.id, "a" * 1001)
    just_right = comment(client, student.user, activity.id, " " + "a" * 1000 + " ")

    assert too_long.status_code == 422
    assert "text" in errors_by_field(too_long)
    assert just_right.status_code == 201
    assert Comment.objects.count() == 1


def test_a_missing_text_is_a_validation_error(client):
    student, _, advising = advised_pair()
    activity = add_activity(advising)

    response = post_json(client, url(activity.id), {}, headers=bearer(student.user))

    assert response.status_code == 422


def test_the_list_is_chronological_paginated_and_carries_the_total_and_the_authors(client):
    student, teacher, advising = advised_pair()
    activity = add_activity(advising)
    now = timezone.now()
    for index in range(5):
        author = student.user if index % 2 == 0 else teacher.user
        created = Comment.objects.create(activity=activity, author=author, text=f"texto {index}")
        # Same instant for all: the id is the tiebreaker.
        Comment.objects.filter(pk=created.pk).update(created_at=now)

    first_page = read(client, student.user, activity.id, limit=2).json()
    last_page = read(client, teacher.user, activity.id, limit=2, offset=4).json()

    assert first_page["count"] == 5
    assert [item["text"] for item in first_page["items"]] == ["texto 0", "texto 1"]
    assert first_page["items"][0]["author"] == {
        "id": student.user.id,
        "full_name": student.user.full_name,
        "type": "student",
    }
    assert first_page["items"][1]["author"]["type"] == "teacher"
    assert set(first_page["items"][0]) == {"id", "text", "created_at", "author"}
    assert [item["text"] for item in last_page["items"]] == ["texto 4"]


def test_an_activity_without_comments_lists_an_empty_page(client):
    student, _, advising = advised_pair()
    activity = add_activity(advising)

    response = read(client, student.user, activity.id)

    assert response.status_code == 200
    assert response.json() == {"items": [], "count": 0}


def test_comments_count_in_the_detail_matches_the_list(client):
    student, teacher, advising = advised_pair()
    activity = add_activity(advising)
    comment(client, student.user, activity.id, "um")
    comment(client, teacher.user, activity.id, "dois")

    detail = client.get(f"/api/atividades/{activity.id}", headers=bearer(student.user)).json()

    assert detail["comments_count"] == read(client, student.user, activity.id).json()["count"] == 2


def test_the_number_of_queries_does_not_grow_with_the_comments(client):
    student, teacher, advising = advised_pair()
    activity = add_activity(advising)
    Comment.objects.create(activity=activity, author=student.user, text="um")

    def queries_for_a_list():
        with CaptureQueriesContext(connection) as queries:
            assert read(client, teacher.user, activity.id).status_code == 200
        return len(queries)

    few = queries_for_a_list()
    for index in range(10):
        author = student.user if index % 2 else teacher.user
        Comment.objects.create(activity=activity, author=author, text=f"mais {index}")

    assert queries_for_a_list() == few


def test_a_completed_activity_can_be_read_but_a_new_comment_gets_409(client):
    student, teacher, advising = advised_pair()
    activity = add_activity(advising)
    comment(client, student.user, activity.id, "antes de concluir")
    Activity.objects.filter(pk=activity.pk).update(status=ActivityStatus.COMPLETED)

    listing = read(client, teacher.user, activity.id)
    refused = comment(client, teacher.user, activity.id, "tarde demais")

    assert listing.status_code == 200
    assert listing.json()["count"] == 1
    assert refused.status_code == 409
    assert refused.json() == COMPLETED
    assert Comment.objects.count() == 1


def test_anonymous_gets_401_and_an_admin_gets_403_on_both_routes(client):
    _, _, advising = advised_pair()
    activity = add_activity(advising)
    admin = bearer(make_user(UserType.ADMIN))

    assert client.get(url(activity.id)).status_code == 401
    assert post_json(client, url(activity.id), {"text": "oi"}).status_code == 401
    assert client.get(url(activity.id), headers=admin).status_code == 403
    assert post_json(client, url(activity.id), {"text": "oi"}, headers=admin).status_code == 403
    assert not Comment.objects.exists()


def test_there_is_no_edit_or_delete_route(client):
    student, _, advising = advised_pair()
    activity = add_activity(advising)
    created = comment(client, student.user, activity.id, "imutável").json()
    target = f"{url(activity.id)}/{created['id']}"

    for method in (client.patch, client.put, client.delete):
        assert method(target, headers=bearer(student.user)).status_code in (404, 405)
    assert Comment.objects.get().text == "imutável"


def hide_it(activity):
    Activity.objects.filter(pk=activity.pk).update(deleted_at=timezone.now())


def archive_it(activity):
    Activity.objects.filter(pk=activity.pk).update(status=ActivityStatus.ARCHIVED)


def end_the_advising(activity):
    close_advising(Advising.objects.get(pk=activity.advising_id), "Encerrada para o teste.")


@pytest.mark.parametrize("hide", [hide_it, archive_it, end_the_advising])
@pytest.mark.parametrize("who", ["student", "teacher"])
def test_a_deleted_archived_or_ended_activity_gets_the_same_404_on_read_and_write(
    client, hide, who
):
    student, teacher, advising = advised_pair()
    activity = add_activity(advising)
    user = student.user if who == "student" else teacher.user
    hide(activity)

    written = comment(client, user, activity.id, "oi")
    listed = read(client, user, activity.id)

    for response in (written, listed):
        assert response.status_code == 404
        assert response.json() == NOT_FOUND
    assert not Comment.objects.exists()


def test_a_missing_activity_gets_the_same_404(client):
    student, _, _ = advised_pair()

    assert comment(client, student.user, 999999, "oi").json() == NOT_FOUND
    assert read(client, student.user, 999999).json() == NOT_FOUND


def test_another_student_or_teacher_gets_the_same_404_and_cannot_comment(client):
    _, _, advising = advised_pair()
    activity = add_activity(advising)
    course = make_course()
    outsiders = [
        make_student(course=course).user,
        make_teacher(courses=[course]).user,
    ]

    for outsider in outsiders:
        for response in (
            comment(client, outsider, activity.id, "oi"),
            read(client, outsider, activity.id),
        ):
            assert response.status_code == 404
            assert response.json() == NOT_FOUND
    assert not Comment.objects.exists()


def test_a_comment_sends_no_notification(client):
    student, teacher, advising = advised_pair()
    activity = add_activity(advising)

    comment(client, student.user, activity.id, "oi")
    comment(client, teacher.user, activity.id, "olá")

    assert not Notification.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_if_the_advising_ends_during_the_action_it_gets_404_and_nothing_is_created(client):
    student, _, advising = advised_pair()
    activity = add_activity(advising)

    response = send_while_the_advising_ends(
        advising, lambda: comment(client, student.user, activity.id, "oi")
    )

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    assert not Comment.objects.exists()


@pytest.mark.parametrize(
    ("final_status", "expected_status", "expected_body"),
    [
        (ActivityStatus.COMPLETED, 409, COMPLETED),
        (ActivityStatus.ARCHIVED, 404, NOT_FOUND),
    ],
)
@pytest.mark.django_db(transaction=True)
def test_if_the_activity_changes_during_the_action_it_gets_409_or_404_and_nothing_is_created(
    client, final_status, expected_status, expected_body
):
    student, _, advising = advised_pair()
    activity = add_activity(advising)
    holding_the_lock = threading.Event()
    failures = []

    def change_the_activity_while_holding_the_lock():
        try:
            with transaction.atomic():
                Advising.objects.select_for_update().get(pk=advising.pk)
                holding_the_lock.set()
                wait_for_a_request_blocked_on_a_lock()
                Activity.objects.filter(pk=activity.pk).update(status=final_status)
        except Exception as error:  # noqa: BLE001 - reported to the main thread
            failures.append(error)
            holding_the_lock.set()
        finally:
            connection.close()

    changing = threading.Thread(target=change_the_activity_while_holding_the_lock)
    changing.start()
    assert holding_the_lock.wait(timeout=10)
    response = comment(client, student.user, activity.id, "oi")
    changing.join(timeout=10)

    assert failures == []
    assert response.status_code == expected_status
    assert response.json() == expected_body
    assert not Comment.objects.exists()
