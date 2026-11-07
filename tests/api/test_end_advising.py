from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db.models import QuerySet
from django.utils import timezone

from apps.accounts.models import UserType
from apps.activities.models import Activity, ActivityStatus
from apps.advising.models import Advising, AdvisingStatus
from apps.advising.services import close_advising
from apps.advising.services import end_advising as end_advising_service
from apps.meetings.models import Meeting, MeetingStatus
from config.errors import ConflictError
from tests.api.helpers import bearer, post_json
from tests.factories import (
    make_activity,
    make_advising,
    make_course,
    make_meeting,
    make_student,
    make_teacher,
    make_user,
)

pytestmark = pytest.mark.django_db

REASON = "Concluímos o ciclo de orientação do TCC."


def end_url(advising_id):
    return f"/api/orientacao/{advising_id}/encerrar"


def end_advising(client, user, advising_id, reason=REASON):
    return post_json(client, end_url(advising_id), {"reason": reason}, headers=bearer(user))


def test_the_advisor_ends_the_advising_and_the_reason_is_recorded(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    advising = make_advising(student=student, teacher=teacher)

    response = end_advising(client, teacher.user, advising.id)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"id", "status", "started_at", "ended_at", "reason"}
    assert body["id"] == advising.id
    assert body["status"] == "ended"
    assert body["reason"] == REASON
    assert body["ended_at"] is not None
    advising.refresh_from_db()
    assert advising.status == AdvisingStatus.ENDED
    assert advising.ended_at is not None
    assert advising.end_reason == REASON
    assert Advising.objects.filter(student=student, status=AdvisingStatus.ACTIVE).count() == 0


def advising_of(teacher):
    return make_advising(student=make_student(course=teacher.courses.first()), teacher=teacher)


def teacher_with_course():
    return make_teacher(courses=[make_course()])


@pytest.mark.parametrize(
    "reason",
    ["", "   ", "curto", "123456789", "  123456789  ", "a" * 501, " " + "a" * 501],
)
def test_a_reason_outside_ten_to_five_hundred_characters_is_rejected_and_changes_nothing(
    client, reason
):
    teacher = teacher_with_course()
    advising = advising_of(teacher)

    response = end_advising(client, teacher.user, advising.id, reason=reason)

    assert response.status_code == 422
    assert "reason" in {error["loc"][-1] for error in response.json()["detail"]}
    advising.refresh_from_db()
    assert advising.status == AdvisingStatus.ACTIVE
    assert advising.ended_at is None
    assert advising.end_reason == ""


def test_a_missing_reason_is_rejected(client):
    teacher = teacher_with_course()
    advising = advising_of(teacher)

    response = post_json(client, end_url(advising.id), {}, headers=bearer(teacher.user))

    assert response.status_code == 422
    advising.refresh_from_db()
    assert advising.status == AdvisingStatus.ACTIVE


@pytest.mark.parametrize("reason", ["1234567890", "a" * 500])
def test_the_limits_of_ten_and_five_hundred_characters_are_accepted(client, reason):
    teacher = teacher_with_course()
    advising = advising_of(teacher)

    response = end_advising(client, teacher.user, advising.id, reason=reason)

    assert response.status_code == 200
    assert response.json()["reason"] == reason


def test_the_spaces_at_the_ends_of_the_reason_are_removed_before_saving(client):
    teacher = teacher_with_course()
    advising = advising_of(teacher)

    response = end_advising(client, teacher.user, advising.id, reason="   motivo válido   ")

    assert response.status_code == 200
    assert response.json()["reason"] == "motivo válido"
    advising.refresh_from_db()
    assert advising.end_reason == "motivo válido"


def activity_with(advising, status, deleted=False):
    activity = make_activity(advising=advising)
    Activity.objects.filter(pk=activity.pk).update(
        status=status, deleted_at=timezone.now() if deleted else None
    )
    return activity


def status_of(activity):
    return Activity.objects.get(pk=activity.pk).status


def test_the_open_activities_are_archived_and_the_completed_and_deleted_stay(client):
    teacher = teacher_with_course()
    advising = advising_of(teacher)
    open_ones = [
        activity_with(advising, ActivityStatus.PENDING),
        activity_with(advising, ActivityStatus.IN_PROGRESS),
        activity_with(advising, ActivityStatus.IN_REVIEW),
    ]
    completed = activity_with(advising, ActivityStatus.COMPLETED)
    deleted = activity_with(advising, ActivityStatus.PENDING, deleted=True)
    already_archived = activity_with(advising, ActivityStatus.ARCHIVED)
    other_advising = advising_of(teacher)
    others = activity_with(other_advising, ActivityStatus.PENDING)

    response = end_advising(client, teacher.user, advising.id)

    assert response.status_code == 200
    assert [status_of(activity) for activity in open_ones] == [ActivityStatus.ARCHIVED] * 3
    assert status_of(completed) == ActivityStatus.COMPLETED
    assert status_of(deleted) == ActivityStatus.PENDING
    assert status_of(already_archived) == ActivityStatus.ARCHIVED
    assert status_of(others) == ActivityStatus.PENDING


def test_a_failure_while_ending_undoes_the_archiving(client):
    teacher = teacher_with_course()
    advising = advising_of(teacher)
    activity = activity_with(advising, ActivityStatus.PENDING)

    with (
        patch.object(Advising, "save", side_effect=RuntimeError("falha ao gravar")),
        pytest.raises(RuntimeError),
    ):
        end_advising_service(teacher, advising.id, REASON)

    advising.refresh_from_db()
    assert advising.status == AdvisingStatus.ACTIVE
    assert advising.end_reason == ""
    assert status_of(activity) == ActivityStatus.PENDING


def test_a_failure_while_archiving_leaves_the_advising_active(client):
    teacher = teacher_with_course()
    advising = advising_of(teacher)
    activity_with(advising, ActivityStatus.PENDING)

    with (
        patch.object(QuerySet, "update", side_effect=RuntimeError("falha ao arquivar")),
        pytest.raises(RuntimeError),
    ):
        end_advising_service(teacher, advising.id, REASON)

    advising.refresh_from_db()
    assert advising.status == AdvisingStatus.ACTIVE
    assert advising.ended_at is None


def test_a_teacher_who_is_not_the_advisor_and_an_unknown_id_look_the_same(client):
    advisor = teacher_with_course()
    advising = advising_of(advisor)
    other_teacher = teacher_with_course()

    from_other = end_advising(client, other_teacher.user, advising.id)
    unknown = end_advising(client, other_teacher.user, advising.id + 1000)

    assert from_other.status_code == unknown.status_code == 404
    assert from_other.content == unknown.content
    assert from_other.json() == {"detail": "Orientação não encontrada."}
    advising.refresh_from_db()
    assert advising.status == AdvisingStatus.ACTIVE


def test_the_student_and_the_administrator_are_denied_and_visitors_are_not_authenticated(client):
    teacher = teacher_with_course()
    advising = advising_of(teacher)

    assert end_advising(client, advising.student.user, advising.id).status_code == 403
    assert end_advising(client, make_user(UserType.ADMIN), advising.id).status_code == 403
    anonymous = post_json(client, end_url(advising.id), {"reason": REASON})
    assert anonymous.status_code == 401
    advising.refresh_from_db()
    assert advising.status == AdvisingStatus.ACTIVE


def test_an_advising_that_already_ended_answers_409_and_keeps_its_data(client):
    teacher = teacher_with_course()
    advising = advising_of(teacher)
    assert end_advising(client, teacher.user, advising.id).status_code == 200
    advising.refresh_from_db()
    first_end, first_reason = advising.ended_at, advising.end_reason

    again = end_advising(client, teacher.user, advising.id, reason="Outro motivo qualquer.")

    assert again.status_code == 409
    assert again.json() == {"detail": "Esta orientação já foi encerrada."}
    advising.refresh_from_db()
    assert advising.ended_at == first_end
    assert advising.end_reason == first_reason


def test_after_the_end_the_student_can_request_again_even_the_same_teacher(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    advising = make_advising(student=student, teacher=teacher)
    assert end_advising(client, teacher.user, advising.id).status_code == 200

    status = client.get("/api/orientacao/minha-solicitacao", headers=bearer(student.user)).json()
    again = post_json(
        client,
        "/api/orientacao/solicitacoes",
        {"teacher_id": teacher.user_id},
        headers=bearer(student.user),
    )

    assert status["can_request"] is True
    assert status["advising"] is None
    assert again.status_code == 201


def test_the_openapi_documents_the_endpoint_the_body_and_the_errors(client):
    schema = client.get("/api/openapi.json").json()

    operation = schema["paths"]["/api/orientacao/{advising_id}/encerrar"]["post"]
    assert {"200", "401", "403", "404", "409", "422"} <= set(operation["responses"])
    assert operation["parameters"][0]["schema"]["type"] == "integer"
    assert operation["description"]
    assert set(schema["components"]["schemas"]["EndedAdvisingOut"]["properties"]) == {
        "id",
        "status",
        "started_at",
        "ended_at",
        "reason",
    }
    reason = schema["components"]["schemas"]["EndAdvisingIn"]["properties"]["reason"]
    assert (reason["minLength"], reason["maxLength"]) == (10, 500)


def meeting_with(advising, status, days):
    return make_meeting(
        advising=advising, status=status, scheduled_at=timezone.now() + timedelta(days=days)
    )


def meeting_status(meeting):
    return Meeting.objects.get(pk=meeting.pk).status


def test_the_pending_and_accepted_future_meetings_are_cancelled_and_the_rest_stays(client):
    teacher = teacher_with_course()
    advising = advising_of(teacher)
    pending = meeting_with(advising, MeetingStatus.PENDING, days=2)
    accepted = meeting_with(advising, MeetingStatus.ACCEPTED, days=5)
    held_pending = meeting_with(advising, MeetingStatus.PENDING, days=-2)
    held_accepted = meeting_with(advising, MeetingStatus.ACCEPTED, days=-1)
    rejected = meeting_with(advising, MeetingStatus.REJECTED, days=4)
    cancelled = meeting_with(advising, MeetingStatus.CANCELLED, days=4)
    other_advising = advising_of(teacher)
    others = meeting_with(other_advising, MeetingStatus.ACCEPTED, days=3)

    response = end_advising(client, teacher.user, advising.id)

    assert response.status_code == 200
    assert meeting_status(pending) == MeetingStatus.CANCELLED
    assert meeting_status(accepted) == MeetingStatus.CANCELLED
    assert meeting_status(held_pending) == MeetingStatus.PENDING
    assert meeting_status(held_accepted) == MeetingStatus.ACCEPTED
    assert meeting_status(rejected) == MeetingStatus.REJECTED
    assert meeting_status(cancelled) == MeetingStatus.CANCELLED
    assert meeting_status(others) == MeetingStatus.ACCEPTED


def test_a_failure_while_ending_also_keeps_the_meetings(client):
    teacher = teacher_with_course()
    advising = advising_of(teacher)
    meeting = meeting_with(advising, MeetingStatus.ACCEPTED, days=3)

    with (
        patch.object(Advising, "save", side_effect=RuntimeError("falha ao gravar")),
        pytest.raises(RuntimeError),
    ):
        end_advising_service(teacher, advising.id, REASON)

    assert meeting_status(meeting) == MeetingStatus.ACCEPTED


def test_an_advising_without_meetings_ends_normally(client):
    teacher = teacher_with_course()
    advising = advising_of(teacher)

    assert end_advising(client, teacher.user, advising.id).status_code == 200


def test_the_cut_off_of_the_meetings_is_the_moment_of_the_end(client):
    teacher = teacher_with_course()
    advising = advising_of(teacher)
    moment = timezone.now().replace(microsecond=0)
    just_after = make_meeting(
        advising=advising, scheduled_at=moment + timedelta(seconds=1), status=MeetingStatus.ACCEPTED
    )
    exactly_then = make_meeting(
        advising=advising, scheduled_at=moment, status=MeetingStatus.ACCEPTED
    )
    just_before = make_meeting(
        advising=advising, scheduled_at=moment - timedelta(seconds=1), status=MeetingStatus.ACCEPTED
    )

    with patch("apps.advising.services.timezone.now", return_value=moment):
        response = end_advising(client, teacher.user, advising.id)

    assert response.status_code == 200
    advising.refresh_from_db()
    assert advising.ended_at == moment
    assert meeting_status(just_after) == MeetingStatus.CANCELLED
    assert meeting_status(exactly_then) == MeetingStatus.ACCEPTED
    assert meeting_status(just_before) == MeetingStatus.ACCEPTED


def test_closing_an_advising_that_already_ended_is_refused_and_keeps_its_data():
    teacher = teacher_with_course()
    advising = advising_of(teacher)
    close_advising(advising, "Primeiro motivo registrado.")
    advising.refresh_from_db()
    first_end = advising.ended_at

    with pytest.raises(ConflictError):
        close_advising(advising, "Segundo motivo qualquer.")

    advising.refresh_from_db()
    assert advising.ended_at == first_end
    assert advising.end_reason == "Primeiro motivo registrado."
