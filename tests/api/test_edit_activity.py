import datetime

import pytest
from django.utils import timezone

from apps.activities.models import Activity, ActivityStatus
from apps.activities.services import FieldChange, edit_activity
from tests.api.helpers import bearer
from tests.factories import make_advising, make_course, make_student, make_teacher

pytestmark = pytest.mark.django_db


def today():
    return timezone.localdate()


def days_from_today(days):
    return today() + datetime.timedelta(days=days)


def url(activity_id):
    return f"/api/atividades/{activity_id}"


def edit(client, user, activity_id, body):
    return client.patch(
        url(activity_id), data=body, content_type="application/json", headers=bearer(user)
    )


def advised_pair():
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    return student, teacher, make_advising(student=student, teacher=teacher)


def add_activity(advising, status=ActivityStatus.PENDING, deadline=None, **extra):
    return Activity.objects.create(
        advising=advising,
        title="Capítulo 2",
        description="Entregar o capítulo 2 do TCC",
        deadline=deadline or days_from_today(7),
        status=status,
        **extra,
    )


def test_the_active_advisor_edits_the_title_and_the_response_has_the_creation_shape(client):
    _, teacher, advising = advised_pair()
    activity = add_activity(advising)

    response = edit(client, teacher.user, activity.id, {"title": "Capítulo 3"})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"id", "title", "description", "deadline", "status", "created_at"}
    assert body["id"] == activity.id
    assert body["title"] == "Capítulo 3"
    assert body["description"] == "Entregar o capítulo 2 do TCC"
    assert body["deadline"] == days_from_today(7).isoformat()
    assert body["status"] == "pending"
    activity.refresh_from_db()
    assert activity.title == "Capítulo 3"


def test_each_field_can_be_edited_alone_or_together(client):
    _, teacher, advising = advised_pair()
    activity = add_activity(advising)

    only_description = edit(client, teacher.user, activity.id, {"description": "Novo texto"})
    only_deadline = edit(
        client, teacher.user, activity.id, {"deadline": days_from_today(20).isoformat()}
    )
    everything = edit(
        client,
        teacher.user,
        activity.id,
        {
            "title": "Tudo",
            "description": "Texto final",
            "deadline": days_from_today(30).isoformat(),
        },
    )

    assert only_description.json()["description"] == "Novo texto"
    assert only_description.json()["title"] == "Capítulo 2"
    assert only_deadline.json()["deadline"] == days_from_today(20).isoformat()
    assert only_deadline.json()["description"] == "Novo texto"
    assert everything.status_code == 200
    activity.refresh_from_db()
    assert (activity.title, activity.description, activity.deadline) == (
        "Tudo",
        "Texto final",
        days_from_today(30),
    )


def test_the_title_and_the_description_are_trimmed_on_edit(client):
    _, teacher, advising = advised_pair()
    activity = add_activity(advising)

    response = edit(
        client, teacher.user, activity.id, {"title": "  Novo \n", "description": "\t Texto  "}
    )

    assert response.json()["title"] == "Novo"
    assert response.json()["description"] == "Texto"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"status": "completed"},
        {"title": None},
        {"description": None},
        {"deadline": None},
        {"title": ""},
        {"title": "   "},
        {"title": "x" * 201},
        {"description": ""},
        {"description": " \n "},
        {"description": "x" * 2001},
        {"deadline": "amanhã"},
        {"deadline": "2026-02-30"},
        {"deadline": ""},
    ],
)
def test_an_invalid_or_empty_body_is_refused_and_changes_nothing(client, body):
    _, teacher, advising = advised_pair()
    activity = add_activity(advising)

    response = edit(client, teacher.user, activity.id, body)

    assert response.status_code == 422
    activity.refresh_from_db()
    assert (activity.title, activity.description, activity.deadline) == (
        "Capítulo 2",
        "Entregar o capítulo 2 do TCC",
        days_from_today(7),
    )


@pytest.mark.parametrize(
    ("field", "value"), [("title", "x"), ("title", "x" * 200), ("description", "x" * 2000)]
)
def test_the_limits_are_accepted_on_edit(client, field, value):
    _, teacher, advising = advised_pair()
    activity = add_activity(advising)

    assert edit(client, teacher.user, activity.id, {field: value}).status_code == 200


def test_the_status_cannot_be_changed_through_the_edit(client):
    _, teacher, advising = advised_pair()
    activity = add_activity(advising)

    response = edit(client, teacher.user, activity.id, {"title": "Novo", "status": "completed"})

    assert response.status_code == 200
    activity.refresh_from_db()
    assert activity.status == ActivityStatus.PENDING


@pytest.mark.parametrize(
    "status", [ActivityStatus.PENDING, ActivityStatus.IN_PROGRESS, ActivityStatus.IN_REVIEW]
)
def test_an_activity_can_be_edited_while_pending_in_progress_or_in_review(client, status):
    _, teacher, advising = advised_pair()
    activity = add_activity(advising, status=status)

    response = edit(client, teacher.user, activity.id, {"title": "Ajustada"})

    assert response.status_code == 200
    assert response.json()["status"] == status.value


def test_a_past_deadline_is_refused_when_it_changes_and_today_is_accepted(client):
    _, teacher, advising = advised_pair()
    activity = add_activity(advising)

    refused = edit(client, teacher.user, activity.id, {"deadline": days_from_today(-1).isoformat()})
    accepted = edit(client, teacher.user, activity.id, {"deadline": today().isoformat()})

    assert refused.status_code == 422
    assert "deadline" in [error["loc"][-1] for error in refused.json()["detail"]]
    assert accepted.status_code == 200
    activity.refresh_from_db()
    assert activity.deadline == today()


def test_an_unchanged_old_deadline_stays_so_the_title_of_a_late_activity_can_be_fixed(client):
    _, teacher, advising = advised_pair()
    old = days_from_today(-10)
    activity = add_activity(advising, deadline=old)

    only_title = edit(client, teacher.user, activity.id, {"title": "Corrigido"})
    same_deadline = edit(
        client, teacher.user, activity.id, {"title": "De novo", "deadline": old.isoformat()}
    )

    assert only_title.status_code == 200
    assert same_deadline.status_code == 200
    activity.refresh_from_db()
    assert (activity.title, activity.deadline) == ("De novo", old)


def test_moving_a_late_deadline_forward_removes_the_lateness(client):
    student, teacher, advising = advised_pair()
    activity = add_activity(advising, deadline=days_from_today(-3))

    def overdue_flag():
        return client.get(url(activity.id), headers=bearer(student.user)).json()["overdue"]

    def late_ids():
        late = client.get("/api/atividades", data={"overdue": "true"}, headers=bearer(teacher.user))
        return [item["id"] for item in late.json()["items"]]

    assert overdue_flag() is True
    assert late_ids() == [activity.id]

    edit(client, teacher.user, activity.id, {"deadline": today().isoformat()})

    assert overdue_flag() is False
    assert late_ids() == []


def test_an_edit_that_changes_nothing_answers_200_and_writes_nothing(client):
    _, teacher, advising = advised_pair()
    activity = add_activity(advising)

    response = edit(
        client,
        teacher.user,
        activity.id,
        {
            "title": "Capítulo 2",
            "description": "Entregar o capítulo 2 do TCC",
            "deadline": days_from_today(7).isoformat(),
        },
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Capítulo 2"
    assert Activity.objects.get(pk=activity.pk).title == "Capítulo 2"


def test_the_service_reports_what_changed_so_that_a_no_change_edit_can_be_told_apart(client):
    _, teacher, advising = advised_pair()
    activity = add_activity(advising)
    new_deadline = days_from_today(9)

    changed = edit_activity(
        teacher,
        activity.id,
        title="Novo",
        description="Entregar o capítulo 2 do TCC",
        deadline=new_deadline,
    )
    unchanged = edit_activity(teacher, activity.id, title="Novo", deadline=new_deadline)

    assert changed.changes == {
        "title": FieldChange(old="Capítulo 2", new="Novo"),
        "deadline": FieldChange(old=days_from_today(7), new=new_deadline),
    }
    assert unchanged.changes == {}
