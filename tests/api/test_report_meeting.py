"""A meeting read from the report of an ended advising: what its modal shows (RF61, RN43, RN47)."""

import pytest
from django.utils import timezone

from apps.meetings.models import MeetingStatus
from tests.api.helpers import bearer
from tests.api.report_builders import add_meeting, ended_advising
from tests.factories import make_admin, make_advising, make_student, make_teacher

pytestmark = pytest.mark.django_db


def meeting_url(advising_id, meeting_id):
    return f"/api/orientacao/historico/{advising_id}/reunioes/{meeting_id}"


def get(client, user, advising_id, meeting_id):
    return client.get(meeting_url(advising_id, meeting_id), headers=bearer(user))


def calendar_item(client, user, meeting):
    day = timezone.localdate(meeting.scheduled_at).isoformat()
    response = client.get(
        "/api/reunioes/calendario", {"date_from": day, "date_to": day}, headers=bearer(user)
    )
    return next(item for item in response.json() if item["id"] == meeting.id)


@pytest.mark.parametrize("party", ["student", "teacher"])
def test_each_party_reads_the_held_meeting_as_the_calendar_shows_it(client, party):
    advising = ended_advising()
    meeting = add_meeting(advising, notes="Rever as considerações finais.")
    user = advising.student.user if party == "student" else advising.teacher.user

    response = get(client, user, advising.id, meeting.id)

    assert response.status_code == 200
    body = response.json()
    assert body == calendar_item(client, user, meeting)
    other = advising.teacher.user if party == "student" else advising.student.user
    assert body["counterpart"] == {"id": other.id, "full_name": other.full_name}
    assert body["author"]["id"] == advising.teacher.user.id
    assert body["situation"] == "held"
    assert body["notes"] == "Rever as considerações finais."
    actions = ("can_answer", "can_edit", "can_cancel", "can_mark_held", "can_edit_notes")
    assert not any(body[action] for action in actions)


def test_a_meeting_marked_held_before_its_time_is_read(client):
    advising = ended_advising()
    meeting = add_meeting(advising, days_from_now=3, held=True)

    response = get(client, advising.student.user, advising.id, meeting.id)

    assert response.status_code == 200
    assert response.json()["marked_held"] is True


@pytest.mark.parametrize(
    "status", [MeetingStatus.PENDING, MeetingStatus.REJECTED, MeetingStatus.CANCELLED]
)
def test_a_meeting_that_did_not_take_place_is_not_found(client, status):
    advising = ended_advising()
    meeting = add_meeting(advising, status=status)

    assert get(client, advising.student.user, advising.id, meeting.id).status_code == 404


def test_an_accepted_meeting_still_to_come_is_not_found(client):
    advising = ended_advising()
    meeting = add_meeting(advising, days_from_now=3)

    assert get(client, advising.student.user, advising.id, meeting.id).status_code == 404


def test_someone_who_is_not_a_party_gets_not_found(client):
    advising = ended_advising()
    meeting = add_meeting(advising)

    for outsider in (make_student().user, make_teacher().user):
        assert get(client, outsider, advising.id, meeting.id).status_code == 404


def test_an_active_advising_has_no_report_meeting(client):
    advising = make_advising()
    meeting = add_meeting(advising)

    assert get(client, advising.student.user, advising.id, meeting.id).status_code == 404


def test_a_meeting_of_another_advising_of_the_same_user_is_not_found(client):
    student = make_student()
    advising = ended_advising(student)
    other = add_meeting(ended_advising(student))

    assert get(client, student.user, advising.id, other.id).status_code == 404


def test_a_visitor_is_unauthorized_and_the_administrator_is_forbidden(client):
    advising = ended_advising()
    url = meeting_url(advising.id, add_meeting(advising).id)

    assert client.get(url).status_code == 401
    assert client.get(url, headers=bearer(make_admin())).status_code == 403
