import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import UserType
from apps.notifications.models import Notification, NotificationType
from tests.api.helpers import bearer
from tests.factories import make_notification, make_student, make_teacher, make_user

pytestmark = pytest.mark.django_db

LIST = "/api/notificacoes"
COUNT = "/api/notificacoes/nao-lidas/contagem"
ALL_READ = "/api/notificacoes/todas-lidas"


def read_url(notification_id):
    return f"/api/notificacoes/{notification_id}/lida"


def listing(client, user, **params):
    return client.get(LIST, params, headers=bearer(user))


def unread_count(client, user):
    return client.get(COUNT, headers=bearer(user)).json()["count"]


def mark_read(client, user, notification_id):
    return client.patch(read_url(notification_id), headers=bearer(user))


def mark_all_read(client, user):
    return client.patch(ALL_READ, headers=bearer(user))


def test_the_list_has_the_agreed_shape_and_only_the_users_own_notifications(client):
    student = make_student().user
    mine = make_notification(
        student,
        type=NotificationType.ACTIVITY_CREATED,
        payload={
            "activity_title": "Capítulo 2",
            "teacher_name": "Carlos Lima",
            "deadline": "2099-12-31",
        },
        reference_type="activity",
        reference_id=7,
    )
    make_notification(make_student().user)

    response = listing(client, student)

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    (item,) = body["items"]
    assert set(item) == {"id", "type", "message", "payload", "reference", "is_read", "created_at"}
    assert item["id"] == mine.id
    assert item["type"] == "activity_created"
    assert item["payload"] == mine.payload
    assert item["reference"] == {"type": "activity", "id": 7}
    assert item["is_read"] is False
    assert item["created_at"].startswith(mine.created_at.strftime("%Y-%m-%dT"))


def test_a_notification_without_a_reference_has_a_null_reference(client):
    teacher = make_teacher().user
    Notification.objects.create(recipient=teacher, type=NotificationType.ACTIVITY_CREATED)

    (item,) = listing(client, teacher).json()["items"]

    assert item["reference"] is None


def test_a_type_without_a_message_of_its_own_falls_back_to_its_label(client):
    student = make_student().user
    make_notification(student, type=NotificationType.ACTIVITY_COMMENTED)

    (item,) = listing(client, student).json()["items"]

    assert item["message"] == "Comentário em atividade"


def test_the_newest_come_first_and_ties_are_broken_by_the_id(client):
    student = make_student().user
    first = make_notification(student)
    second = make_notification(student)
    third = make_notification(student)

    ids = [item["id"] for item in listing(client, student).json()["items"]]

    assert ids == [third.id, second.id, first.id]


@pytest.mark.parametrize(
    ("type_", "payload"),
    [
        (
            NotificationType.ACTIVITY_CREATED,
            {"activity_title": "A", "teacher_name": "B", "deadline": None},
        ),
        (
            NotificationType.ACTIVITY_CREATED,
            {"activity_title": "A", "teacher_name": "B", "deadline": "não é uma data"},
        ),
        (
            NotificationType.ACTIVITY_CHANGED,
            {"activity_title": "A", "teacher_name": "B", "changes": None},
        ),
        (
            NotificationType.ACTIVITY_CHANGED,
            {"activity_title": "A", "teacher_name": "B", "changes": [42]},
        ),
        (
            NotificationType.MEETING_REQUESTED,
            {"author_name": "A", "author_type": "student", "scheduled_at": "ontem"},
        ),
        (NotificationType.ADVISING_ENDED, {"teacher_name": "A", "archived_activities": "x"}),
    ],
)
def test_a_malformed_payload_never_breaks_the_list_and_falls_back_to_the_label(
    client, type_, payload
):
    student = make_student().user
    make_notification(student, type=type_, payload=payload)
    make_notification(student, type=NotificationType.ACTIVITY_COMMENTED)

    response = listing(client, student)

    assert response.status_code == 200
    messages = {item["type"]: item["message"] for item in response.json()["items"]}
    assert messages[type_] == NotificationType(type_).label


def test_notifications_created_at_the_same_instant_are_ordered_by_the_id_newest_first(client):
    student = make_student().user
    created = [make_notification(student) for _ in range(3)]
    same_instant = created[0].created_at
    Notification.objects.filter(recipient=student).update(created_at=same_instant)

    ids = [item["id"] for item in listing(client, student).json()["items"]]

    assert ids == [created[2].id, created[1].id, created[0].id]


def test_the_list_is_paginated_with_the_total(client):
    student = make_student().user
    for _ in range(5):
        make_notification(student)

    page = listing(client, student, limit=2, offset=2).json()

    assert page["count"] == 5
    assert len(page["items"]) == 2


@pytest.mark.parametrize(("is_read", "expected"), [("true", {"read"}), ("false", {"unread"})])
def test_the_list_filters_by_is_read(client, is_read, expected):
    student = make_student().user
    make_notification(student, is_read=True, payload={"mark": "read"})
    make_notification(student, is_read=False, payload={"mark": "unread"})

    items = listing(client, student, is_read=is_read).json()["items"]

    assert {item["payload"]["mark"] for item in items} == expected


def test_without_the_filter_both_read_and_unread_are_listed(client):
    student = make_student().user
    make_notification(student, is_read=True)
    make_notification(student, is_read=False)

    assert listing(client, student).json()["count"] == 2


def test_the_unread_count_matches_the_unread_list_and_ignores_other_users(client):
    student = make_student().user
    for _ in range(3):
        make_notification(student)
    make_notification(student, is_read=True)
    make_notification(make_student().user)

    assert unread_count(client, student) == 3
    assert (
        unread_count(client, student) == listing(client, student, is_read="false").json()["count"]
    )


def test_the_count_is_zero_without_notifications(client):
    assert unread_count(client, make_teacher().user) == 0


def test_marking_one_as_read_returns_it_and_updates_the_count(client):
    student = make_student().user
    target = make_notification(student)
    make_notification(student)

    response = mark_read(client, student, target.id)

    assert response.status_code == 200
    assert response.json()["id"] == target.id
    assert response.json()["is_read"] is True
    assert set(response.json()) == {
        "id",
        "type",
        "message",
        "payload",
        "reference",
        "is_read",
        "created_at",
    }
    target.refresh_from_db()
    assert target.is_read is True
    assert unread_count(client, student) == 1


def test_marking_one_as_read_twice_is_idempotent_and_writes_nothing_the_second_time(client):
    student = make_student().user
    target = make_notification(student)
    mark_read(client, student, target.id)

    with CaptureQueriesContext(connection) as queries:
        response = mark_read(client, student, target.id)

    assert response.status_code == 200
    assert response.json()["is_read"] is True
    assert not [q for q in queries if q["sql"].lstrip().upper().startswith("UPDATE")]


def test_marking_someone_elses_or_a_missing_notification_gets_the_same_404(client):
    student = make_student().user
    other = make_notification(make_student().user)

    someone_elses = mark_read(client, student, other.id)
    missing = mark_read(client, student, 999999)

    assert someone_elses.status_code == 404
    assert missing.status_code == 404
    assert someone_elses.json() == missing.json() == {"detail": "Notificação não encontrada."}
    other.refresh_from_db()
    assert other.is_read is False


def test_marking_all_as_read_changes_only_the_users_unread_and_says_how_many(client):
    student = make_student().user
    other = make_student().user
    make_notification(student)
    make_notification(student)
    make_notification(student, is_read=True)
    make_notification(other)

    response = mark_all_read(client, student)

    assert response.status_code == 200
    assert response.json() == {"count": 2}
    assert unread_count(client, student) == 0
    assert unread_count(client, other) == 1


def test_marking_all_as_read_again_changes_nothing(client):
    student = make_student().user
    make_notification(student)
    mark_all_read(client, student)

    assert mark_all_read(client, student).json() == {"count": 0}


@pytest.mark.parametrize("who", ["student", "teacher"])
def test_students_and_teachers_use_every_route(client, who):
    user = make_student().user if who == "student" else make_teacher().user
    target = make_notification(user)

    assert listing(client, user).status_code == 200
    assert client.get(COUNT, headers=bearer(user)).status_code == 200
    assert mark_read(client, user, target.id).status_code == 200
    assert mark_all_read(client, user).status_code == 200


ROUTES = [
    ("get", LIST),
    ("get", COUNT),
    ("patch", read_url(1)),
    ("patch", ALL_READ),
]


@pytest.mark.parametrize(("method", "url"), ROUTES)
def test_an_admin_gets_a_403(client, method, url):
    admin = make_user(UserType.ADMIN)

    response = getattr(client, method)(url, headers=bearer(admin))

    assert response.status_code == 403


@pytest.mark.parametrize(("method", "url"), ROUTES)
def test_an_anonymous_request_gets_a_401(client, method, url):
    assert getattr(client, method)(url).status_code == 401


def test_the_number_of_queries_does_not_depend_on_the_number_of_notifications(client):
    student = make_student().user
    make_notification(student)
    listing(client, student)  # warms up the caches

    def count_queries():
        with CaptureQueriesContext(connection) as queries:
            assert listing(client, student).status_code == 200
        return len(queries)

    with_one = count_queries()
    for _ in range(20):
        make_notification(student)

    assert count_queries() == with_one


# The payload of every type that has events today: what the list documents for the screen (#54).
PAYLOAD_FIELDS = {
    "advising_request_received": ["student_name"],
    "advising_request_answered": ["teacher_name", "decision"],
    "activity_created": ["activity_title", "deadline", "teacher_name"],
    "activity_changed": ["activity_title", "teacher_name", "changes", "field", "old", "new"],
    "activity_deleted": ["activity_title", "teacher_name"],
    "advising_ended": ["teacher_name", "archived_activities", "cancelled_meetings"],
    "meeting_requested": ["author_name", "author_type", "scheduled_at", "description"],
    "meeting_answered": ["responder_name", "decision", "reason", "scheduled_at"],
}


def operation(client, path, method):
    return client.get("/api/openapi.json").json()["paths"][path][method]


def test_the_openapi_documents_the_routes_and_their_errors(client):
    listing_route = operation(client, LIST, "get")
    count_route = operation(client, COUNT, "get")
    one_route = operation(client, "/api/notificacoes/{notification_id}/lida", "patch")
    all_route = operation(client, ALL_READ, "patch")

    assert {"200", "401", "403", "422"} <= set(listing_route["responses"])
    assert {"200", "401", "403"} <= set(count_route["responses"])
    assert {"200", "401", "403", "404"} <= set(one_route["responses"])
    assert {"200", "401", "403"} <= set(all_route["responses"])
    names = {parameter["name"] for parameter in listing_route["parameters"]}
    assert {"is_read", "limit", "offset"} <= names


@pytest.mark.parametrize(("type_", "fields"), PAYLOAD_FIELDS.items())
def test_the_openapi_documents_the_payload_of_each_type(client, type_, fields):
    description = operation(client, LIST, "get")["description"]

    assert type_ in description
    for field in fields:
        assert field in description
