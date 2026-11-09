"""The notifications of the activity events: creation (RF35), edit and delete (RF52)."""

from unittest.mock import patch

import pytest
from django.test import Client

from apps.activities.models import Activity, ActivityStatus
from apps.notifications.models import Notification
from tests.api.helpers import CREATE_NOTIFICATION, bearer, inbox, post_json
from tests.factories import make_advising, make_course, make_student, make_teacher

pytestmark = pytest.mark.django_db


def advised_pair():
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    return student, teacher, make_advising(student=student, teacher=teacher)


def create_activity(client, teacher, student, **overrides):
    body = {
        "student_id": student.user_id,
        "title": "Capítulo 2",
        "description": "Entregar o capítulo 2 do TCC",
        "deadline": "2099-12-31",
        **overrides,
    }
    return post_json(client, "/api/atividades", body, headers=bearer(teacher.user))


def test_creating_an_activity_notifies_only_the_student_with_the_snapshot(client):
    student, teacher, _ = advised_pair()

    response = create_activity(client, teacher, student)

    assert response.status_code == 201
    (item,) = inbox(client, student.user)
    assert item["type"] == "activity_created"
    assert item["reference"] == {"type": "activity", "id": response.json()["id"]}
    assert item["is_read"] is False
    assert item["payload"] == {
        "activity_title": "Capítulo 2",
        "deadline": "2099-12-31",
        "teacher_name": teacher.user.full_name,
    }
    assert item["message"] == (
        f'{teacher.user.full_name} criou a atividade "Capítulo 2" para você, '
        "com prazo em 31/12/2099."
    )
    assert inbox(client, teacher.user) == []
    assert Notification.objects.count() == 1


def test_the_snapshot_keeps_the_title_as_it_was_when_the_activity_was_created(client):
    student, teacher, _ = advised_pair()
    activity_id = create_activity(client, teacher, student).json()["id"]
    Activity.objects.filter(pk=activity_id).update(title="Título mudado depois")

    (item,) = inbox(client, student.user)

    assert item["payload"]["activity_title"] == "Capítulo 2"


def test_a_creation_that_fails_validation_notifies_nobody(client):
    student, teacher, _ = advised_pair()

    response = create_activity(client, teacher, student, deadline="2000-01-01")

    assert response.status_code == 422
    assert not Notification.objects.exists()


def test_a_creation_for_a_student_without_an_advising_notifies_nobody(client):
    _, teacher, _ = advised_pair()
    loner = make_student()

    response = create_activity(client, teacher, loner)

    assert response.status_code == 404
    assert not Notification.objects.exists()


def test_a_failing_notification_rolls_the_creation_back():
    student, teacher, _ = advised_pair()
    quiet_client = Client(raise_request_exception=False)

    with patch(CREATE_NOTIFICATION, side_effect=RuntimeError("boom")):
        response = create_activity(quiet_client, teacher, student)

    assert response.status_code == 500
    assert not Activity.objects.exists()
    assert not Notification.objects.exists()


def add_activity(advising, **extra):
    return Activity.objects.create(
        advising=advising,
        title="Capítulo 2",
        description="Entregar o capítulo 2 do TCC",
        deadline="2099-12-31",
        **extra,
    )


def edit(client, teacher, activity, body):
    return client.patch(
        f"/api/atividades/{activity.id}",
        data=body,
        content_type="application/json",
        headers=bearer(teacher.user),
    )


def delete(client, teacher, activity):
    return client.delete(f"/api/atividades/{activity.id}", headers=bearer(teacher.user))


def test_editing_the_title_notifies_only_the_student_with_what_changed(client):
    student, teacher, advising = advised_pair()
    activity = add_activity(advising)

    response = edit(client, teacher, activity, {"title": "Capítulo 3"})

    assert response.status_code == 200
    (item,) = inbox(client, student.user)
    assert item["type"] == "activity_changed"
    assert item["reference"] == {"type": "activity", "id": activity.id}
    assert item["payload"] == {
        "activity_title": "Capítulo 3",
        "teacher_name": teacher.user.full_name,
        "changes": [{"field": "title", "old": "Capítulo 2", "new": "Capítulo 3"}],
    }
    assert item["message"] == (
        f'{teacher.user.full_name} alterou a atividade "Capítulo 3": '
        'título de "Capítulo 2" para "Capítulo 3".'
    )
    assert inbox(client, teacher.user) == []
    assert Notification.objects.count() == 1


def test_editing_every_field_lists_them_in_a_fixed_order_with_the_old_and_the_new_value(client):
    student, teacher, advising = advised_pair()
    activity = add_activity(advising)
    long_text = "Texto novo. " * 100

    edit(
        client,
        teacher,
        activity,
        {"deadline": "2100-01-05", "description": long_text.strip(), "title": "Capítulo 3"},
    )

    (item,) = inbox(client, student.user)
    assert item["payload"]["changes"] == [
        {"field": "title", "old": "Capítulo 2", "new": "Capítulo 3"},
        {
            "field": "description",
            "old": "Entregar o capítulo 2 do TCC",
            "new": long_text.strip(),
        },
        {"field": "deadline", "old": "2099-12-31", "new": "2100-01-05"},
    ]
    assert item["message"] == (
        f'{teacher.user.full_name} alterou a atividade "Capítulo 3": '
        'título de "Capítulo 2" para "Capítulo 3"; a descrição foi alterada; '
        "prazo de 31/12/2099 para 05/01/2100."
    )
    assert "Texto novo" not in item["message"]


def test_only_the_fields_that_changed_are_listed(client):
    student, teacher, advising = advised_pair()
    activity = add_activity(advising)

    edit(client, teacher, activity, {"title": "Capítulo 2", "deadline": "2100-01-05"})

    (item,) = inbox(client, student.user)
    assert [change["field"] for change in item["payload"]["changes"]] == ["deadline"]


def test_an_edit_that_changes_nothing_notifies_nobody(client):
    student, teacher, advising = advised_pair()
    activity = add_activity(advising)

    response = edit(
        client,
        teacher,
        activity,
        {
            "title": "Capítulo 2",
            "description": "Entregar o capítulo 2 do TCC",
            "deadline": "2099-12-31",
        },
    )

    assert response.status_code == 200
    assert not Notification.objects.exists()


@pytest.mark.parametrize("status", [ActivityStatus.COMPLETED])
def test_an_edit_that_fails_notifies_nobody(client, status):
    student, teacher, advising = advised_pair()
    activity = add_activity(advising, status=status)

    response = edit(client, teacher, activity, {"title": "Outro"})

    assert response.status_code == 409
    assert not Notification.objects.exists()


def test_an_edit_with_an_invalid_body_or_a_missing_activity_notifies_nobody(client):
    student, teacher, advising = advised_pair()
    activity = add_activity(advising)

    invalid = edit(client, teacher, activity, {"deadline": "2000-01-01"})
    missing = client.patch(
        "/api/atividades/999999",
        data={"title": "Outro"},
        content_type="application/json",
        headers=bearer(teacher.user),
    )

    assert invalid.status_code == 422
    assert missing.status_code == 404
    assert not Notification.objects.exists()


def test_a_failing_notification_rolls_the_edit_back():
    student, teacher, advising = advised_pair()
    activity = add_activity(advising)
    quiet_client = Client(raise_request_exception=False)

    with patch(CREATE_NOTIFICATION, side_effect=RuntimeError("boom")):
        response = edit(quiet_client, teacher, activity, {"title": "Capítulo 3"})

    assert response.status_code == 500
    activity.refresh_from_db()
    assert activity.title == "Capítulo 2"
    assert not Notification.objects.exists()


def test_deleting_an_activity_always_notifies_the_student(client):
    student, teacher, advising = advised_pair()
    activity = add_activity(advising)

    response = delete(client, teacher, activity)

    assert response.status_code == 204
    (item,) = inbox(client, student.user)
    assert item["type"] == "activity_deleted"
    assert item["reference"] == {"type": "activity", "id": activity.id}
    assert item["payload"] == {
        "activity_title": "Capítulo 2",
        "teacher_name": teacher.user.full_name,
    }
    assert item["message"] == f'{teacher.user.full_name} excluiu a atividade "Capítulo 2".'
    assert inbox(client, teacher.user) == []
    assert Notification.objects.count() == 1


def test_a_delete_that_fails_notifies_nobody(client):
    student, teacher, advising = advised_pair()
    completed = add_activity(advising, status=ActivityStatus.COMPLETED)

    conflict = delete(client, teacher, completed)
    missing = client.delete("/api/atividades/999999", headers=bearer(teacher.user))

    assert conflict.status_code == 409
    assert missing.status_code == 404
    assert not Notification.objects.exists()


def test_a_failing_notification_rolls_the_delete_back():
    student, teacher, advising = advised_pair()
    activity = add_activity(advising)
    quiet_client = Client(raise_request_exception=False)

    with patch(CREATE_NOTIFICATION, side_effect=RuntimeError("boom")):
        response = delete(quiet_client, teacher, activity)

    assert response.status_code == 500
    activity.refresh_from_db()
    assert activity.deleted_at is None
    assert not Notification.objects.exists()
