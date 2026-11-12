import datetime
from zoneinfo import ZoneInfo

import pytest
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.deactivation import deactivate_user
from apps.accounts.models import UserType
from apps.advising.models import Advising, AdvisingStatus
from apps.meetings.models import Meeting, MeetingStatus
from apps.notifications.models import Notification
from tests.api.helpers import bearer, post_json
from tests.factories import (
    make_advising,
    make_course,
    make_meeting,
    make_student,
    make_teacher,
    make_user,
)

pytestmark = pytest.mark.django_db

SANTAREM = ZoneInfo("America/Santarem")


def url(meeting):
    return f"/api/reunioes/{meeting.id}/realizada"


def mark(client, user, meeting):
    return post_json(client, url(meeting), {}, headers=bearer(user))


def in_days(days, hour=10):
    day = (timezone.now().astimezone(SANTAREM) + datetime.timedelta(days=days)).date()
    return datetime.datetime(day.year, day.month, day.day, hour, 0, tzinfo=SANTAREM)


def advised_pair():
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    return student, teacher, make_advising(student=student, teacher=teacher)


def accepted(advising, days=3, author=None):
    return make_meeting(advising, MeetingStatus.ACCEPTED, scheduled_at=in_days(days), author=author)


def test_the_advisor_marks_an_accepted_future_meeting_and_the_moment_and_author_are_kept(client):
    _, teacher, advising = advised_pair()
    meeting = accepted(advising)

    before = timezone.now()
    response = mark(client, teacher.user, meeting)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == meeting.id
    assert body["status"] == "accepted"
    assert body["held_at"].endswith("-03:00")
    meeting.refresh_from_db()
    assert before <= meeting.held_at <= timezone.now()
    assert meeting.held_by == teacher.user
    assert meeting.status == MeetingStatus.ACCEPTED


def test_the_advisor_marks_a_meeting_the_student_requested(client):
    student, teacher, advising = advised_pair()
    meeting = accepted(advising, author=student.user)

    assert mark(client, teacher.user, meeting).status_code == 200


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (MeetingStatus.PENDING, "Só uma reunião aceita pode ser marcada como realizada."),
        (MeetingStatus.REJECTED, "Só uma reunião aceita pode ser marcada como realizada."),
        (MeetingStatus.CANCELLED, "Esta reunião foi cancelada."),
    ],
)
def test_a_pending_rejected_or_cancelled_meeting_cannot_be_marked(client, status, message):
    _, teacher, advising = advised_pair()
    meeting = make_meeting(advising, status, scheduled_at=in_days(3))
    if status == MeetingStatus.CANCELLED:
        Meeting.objects.filter(pk=meeting.pk).update(cancellation_reason="Imprevisto.")

    response = mark(client, teacher.user, meeting)

    assert response.status_code == 409
    assert response.json() == {"detail": message}
    meeting.refresh_from_db()
    assert meeting.held_at is None and meeting.held_by is None


def test_an_already_marked_meeting_cannot_be_marked_again(client):
    _, teacher, advising = advised_pair()
    meeting = accepted(advising)
    mark(client, teacher.user, meeting)
    first = Meeting.objects.get(pk=meeting.pk).held_at

    response = mark(client, teacher.user, meeting)

    assert response.status_code == 409
    assert response.json() == {"detail": "Esta reunião já foi marcada como realizada."}
    assert Meeting.objects.get(pk=meeting.pk).held_at == first


def test_a_meeting_whose_time_passed_cannot_be_marked(client):
    _, teacher, advising = advised_pair()
    meeting = accepted(advising, days=-1)

    response = mark(client, teacher.user, meeting)

    assert response.status_code == 409
    assert response.json() == {"detail": "O horário desta reunião já passou."}
    assert Meeting.objects.get(pk=meeting.pk).held_at is None


def test_the_student_cannot_mark_and_nobody_unauthenticated_either(client):
    student, _, advising = advised_pair()
    meeting = accepted(advising)

    assert mark(client, student.user, meeting).status_code == 403
    assert mark(client, make_user(UserType.ADMIN), meeting).status_code == 403
    assert post_json(client, url(meeting), {}).status_code == 401
    assert Meeting.objects.get(pk=meeting.pk).held_at is None


def test_another_teacher_and_an_unknown_meeting_get_the_same_404(client):
    _, _, advising = advised_pair()
    meeting = accepted(advising)
    other_teacher = make_teacher()

    other = mark(client, other_teacher.user, meeting)
    unknown = post_json(
        client, "/api/reunioes/999999/realizada", {}, headers=bearer(other_teacher.user)
    )

    assert other.status_code == 404
    assert other.json() == unknown.json() == {"detail": "Reunião não encontrada."}
    assert Meeting.objects.get(pk=meeting.pk).held_at is None


def test_without_an_active_advising_the_marking_is_refused(client):
    _, teacher, advising = advised_pair()
    meeting = accepted(advising)
    Advising.objects.filter(pk=advising.pk).update(
        status=AdvisingStatus.ENDED, ended_at=timezone.now(), end_reason="Encerrada no teste."
    )

    response = mark(client, teacher.user, meeting)

    assert response.status_code == 404
    assert Meeting.objects.get(pk=meeting.pk).held_at is None


def test_the_marking_sends_no_notification(client):
    _, teacher, advising = advised_pair()
    meeting = accepted(advising)
    before = Notification.objects.count()

    mark(client, teacher.user, meeting)

    assert Notification.objects.count() == before


def test_a_marked_meeting_can_no_longer_be_edited_or_cancelled(client):
    student, teacher, advising = advised_pair()
    meeting = accepted(advising, author=student.user)
    mark(client, teacher.user, meeting)

    edited = client.patch(
        f"/api/reunioes/{meeting.id}",
        {"description": "Outra pauta"},
        content_type="application/json",
        headers=bearer(student.user),
    )
    cancelled = post_json(
        client,
        f"/api/reunioes/{meeting.id}/cancelar",
        {"reason": "Mudei de ideia."},
        headers=bearer(teacher.user),
    )

    for response in (edited, cancelled):
        assert response.status_code == 409
        assert response.json() == {"detail": "Esta reunião já foi marcada como realizada."}
    meeting.refresh_from_db()
    assert meeting.status == MeetingStatus.ACCEPTED and meeting.description != "Outra pauta"


def test_a_marked_meeting_leaves_the_next_meetings_of_both_dashboards(client):
    student, teacher, advising = advised_pair()
    kept = accepted(advising, days=5)
    marked = accepted(advising, days=3)
    mark(client, teacher.user, marked)

    student_dashboard = client.get("/api/painel/aluno", headers=bearer(student.user)).json()
    teacher_dashboard = client.get("/api/painel/professor", headers=bearer(teacher.user)).json()

    for dashboard in (student_dashboard, teacher_dashboard):
        ids = [item["id"] for item in dashboard["next_meetings"]["items"]]
        assert ids == [kept.id]


def test_ending_the_advising_does_not_cancel_a_marked_meeting_and_still_cancels_the_others(client):
    _, teacher, advising = advised_pair()
    marked = accepted(advising, days=3)
    other = accepted(advising, days=5)
    pending = make_meeting(advising, MeetingStatus.PENDING, scheduled_at=in_days(6))
    mark(client, teacher.user, marked)

    response = post_json(
        client,
        f"/api/orientacao/{advising.id}/encerrar",
        {"reason": "Trabalho concluído com sucesso."},
        headers=bearer(teacher.user),
    )

    assert response.status_code == 200
    marked.refresh_from_db()
    assert marked.status == MeetingStatus.ACCEPTED and not marked.cancelled_by_system
    assert Meeting.objects.get(pk=other.pk).status == MeetingStatus.CANCELLED
    assert Meeting.objects.get(pk=pending.pk).status == MeetingStatus.CANCELLED


def test_deactivating_a_user_does_not_cancel_a_marked_meeting(client):
    student, teacher, advising = advised_pair()
    marked = accepted(advising, days=3)
    other = accepted(advising, days=5)
    mark(client, teacher.user, marked)

    deactivate_user(student.user)

    marked.refresh_from_db()
    assert marked.status == MeetingStatus.ACCEPTED and marked.held_at is not None
    assert Meeting.objects.get(pk=other.pk).status == MeetingStatus.CANCELLED


def test_the_listing_tells_if_the_meeting_was_marked_and_who_can_mark(client):
    student, teacher, advising = advised_pair()
    future = accepted(advising, days=3)
    marked = accepted(advising, days=4)
    pending = make_meeting(advising, MeetingStatus.PENDING, scheduled_at=in_days(5))
    past = accepted(advising, days=-2)
    mark(client, teacher.user, marked)

    def flags(user):
        body = client.get("/api/reunioes", {"limit": 100}, headers=bearer(user)).json()
        return {
            item["id"]: (item["marked_held"], item["can_mark_held"], item["situation"])
            for item in body["items"]
        }

    teacher_view = flags(teacher.user)
    student_view = flags(student.user)

    assert teacher_view[future.id] == (False, True, "accepted")
    assert teacher_view[marked.id] == (True, False, "held")
    assert teacher_view[pending.id] == (False, False, "pending")
    assert teacher_view[past.id] == (False, False, "held")
    assert all(not can for _, can, _ in student_view.values())
    assert student_view[marked.id][0] is True


def test_the_marked_meeting_shows_as_held_and_cannot_be_edited_or_cancelled_in_the_listing(client):
    _, teacher, advising = advised_pair()
    marked = accepted(advising, days=3, author=teacher.user)
    mark(client, teacher.user, marked)

    body = client.get("/api/reunioes", headers=bearer(teacher.user)).json()
    item = body["items"][0]

    assert item["situation"] == "held"
    assert (item["can_answer"], item["can_edit"], item["can_cancel"]) == (False, False, False)


def test_the_calendar_reading_also_carries_the_mark_and_the_capability(client):
    _, teacher, advising = advised_pair()
    future = accepted(advising, days=3)
    day = in_days(3).date().isoformat()

    (item,) = client.get(
        "/api/reunioes/calendario",
        {"date_from": day, "date_to": day},
        headers=bearer(teacher.user),
    ).json()
    mark(client, teacher.user, future)
    (after,) = client.get(
        "/api/reunioes/calendario",
        {"date_from": day, "date_to": day},
        headers=bearer(teacher.user),
    ).json()

    assert (item["marked_held"], item["can_mark_held"]) == (False, True)
    assert (after["marked_held"], after["can_mark_held"], after["situation"]) == (
        True,
        False,
        "held",
    )


def test_a_meeting_held_by_time_is_unchanged_and_needs_no_marking(client):
    _, teacher, advising = advised_pair()
    past = accepted(advising, days=-1)

    body = client.get("/api/reunioes", headers=bearer(teacher.user)).json()

    assert body["items"][0]["situation"] == "held"
    assert body["items"][0]["marked_held"] is False
    assert Meeting.objects.get(pk=past.pk).held_at is None


def test_the_number_of_queries_of_the_marking_does_not_depend_on_other_meetings(client):
    _, teacher, advising = advised_pair()
    first = accepted(advising, days=3)
    for _ in range(5):
        accepted(advising, days=4)
    second = accepted(advising, days=6)

    with CaptureQueriesContext(connection) as one:
        assert mark(client, teacher.user, first).status_code == 200
    with CaptureQueriesContext(connection) as other:
        assert mark(client, teacher.user, second).status_code == 200

    assert len(one) == len(other)


def test_the_openapi_documents_the_marking(client):
    schema = client.get("/api/openapi.json").json()

    operation = schema["paths"]["/api/reunioes/{meeting_id}/realizada"]["post"]
    assert {"200", "401", "403", "404", "409"} <= set(operation["responses"])
    assert "RN43" in operation["description"]
    item = schema["components"]["schemas"]["MeetingItemOut"]["properties"]
    assert {"marked_held", "can_mark_held"} <= set(item)


def test_the_database_refuses_a_held_mark_without_author_or_on_a_meeting_that_is_not_accepted():
    _, teacher, advising = advised_pair()
    pending = make_meeting(advising, MeetingStatus.PENDING, scheduled_at=in_days(3))
    accepted_meeting = accepted(advising)

    with pytest.raises(IntegrityError), transaction.atomic():
        Meeting.objects.filter(pk=pending.pk).update(held_at=timezone.now(), held_by=teacher.user)
    with pytest.raises(IntegrityError), transaction.atomic():
        Meeting.objects.filter(pk=accepted_meeting.pk).update(held_at=timezone.now())
    with pytest.raises(IntegrityError), transaction.atomic():
        Meeting.objects.filter(pk=accepted_meeting.pk).update(held_by=teacher.user)
