from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone
from ninja_jwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from ninja_jwt.tokens import RefreshToken

from apps.accounts.deactivation import ALREADY_DEACTIVATED_MESSAGE, deactivate_user
from apps.accounts.models import User
from apps.activities.models import Activity, ActivityStatus
from apps.advising.models import (
    AdvisingRequest,
    AdvisingRequestStatus,
    AdvisingStatus,
)
from apps.advising.services import SYSTEM_END_REASON, close_advising
from apps.meetings.models import SYSTEM_CANCELLATION_REASON, Meeting, MeetingStatus
from config.errors import ConflictError
from tests.api.helpers import LOGIN_URL, ME_URL, bearer, post_json
from tests.factories import (
    DEFAULT_PASSWORD,
    make_activity,
    make_advising,
    make_advising_request,
    make_course,
    make_meeting,
    make_student,
    make_teacher,
)

pytestmark = pytest.mark.django_db


def make_open_advising(student=None, teacher=None):
    """An active advising with one activity in each open status, one concluded, and meetings."""
    advising = make_advising(student=student, teacher=teacher)
    for status in (
        ActivityStatus.PENDING,
        ActivityStatus.IN_PROGRESS,
        ActivityStatus.IN_REVIEW,
        ActivityStatus.COMPLETED,
    ):
        activity = make_activity(advising)
        Activity.objects.filter(pk=activity.pk).update(status=status)
    make_meeting(advising, status=MeetingStatus.PENDING)
    make_meeting(advising, status=MeetingStatus.ACCEPTED)
    make_meeting(
        advising,
        status=MeetingStatus.ACCEPTED,
        scheduled_at=timezone.now() - timedelta(days=2),
    )
    return advising


def assert_ended_by_the_system(advising):
    advising.refresh_from_db()
    assert advising.status == AdvisingStatus.ENDED
    assert advising.ended_at is not None
    assert advising.end_reason == SYSTEM_END_REASON
    open_statuses = {
        ActivityStatus.PENDING,
        ActivityStatus.IN_PROGRESS,
        ActivityStatus.IN_REVIEW,
    }
    assert not Activity.objects.filter(advising=advising, status__in=open_statuses).exists()
    assert Activity.objects.filter(advising=advising, status=ActivityStatus.ARCHIVED).count() == 3
    assert Activity.objects.filter(advising=advising, status=ActivityStatus.COMPLETED).count() == 1
    cancelled = Meeting.objects.filter(advising=advising, status=MeetingStatus.CANCELLED)
    assert cancelled.count() == 2
    assert all(meeting.cancelled_by_system for meeting in cancelled)
    assert {meeting.cancellation_reason for meeting in cancelled} == {SYSTEM_CANCELLATION_REASON}
    assert Meeting.objects.filter(advising=advising, status=MeetingStatus.ACCEPTED).count() == 1


def test_deactivating_a_student_ends_the_advising_and_what_depends_on_it():
    student = make_student()
    advising = make_open_advising(student=student)

    deactivate_user(student.user)

    student.user.refresh_from_db()
    assert student.user.is_active is False
    assert_ended_by_the_system(advising)


def test_deactivating_a_teacher_ends_all_of_the_active_advisings():
    teacher = make_teacher()
    first = make_open_advising(teacher=teacher)
    second = make_open_advising(teacher=teacher)
    ended_before = make_advising(teacher=teacher)
    close_advising(ended_before, "Concluída antes.")

    deactivate_user(teacher.user)

    teacher.user.refresh_from_db()
    assert teacher.user.is_active is False
    assert_ended_by_the_system(first)
    assert_ended_by_the_system(second)
    ended_before.refresh_from_db()
    assert ended_before.end_reason == "Concluída antes."


def test_the_pending_requests_of_a_deactivated_student_are_cancelled():
    student = make_student()
    pending = make_advising_request(student=student)
    other = make_advising_request()

    deactivate_user(student.user)

    pending.refresh_from_db()
    other.refresh_from_db()
    assert pending.status == AdvisingRequestStatus.UNANSWERED
    assert other.status == AdvisingRequestStatus.PENDING


def test_the_pending_requests_to_a_deactivated_teacher_are_cancelled_and_free_the_students():
    teacher = make_teacher()
    first = make_advising_request(teacher=teacher)
    second = make_advising_request(teacher=teacher)
    answered = make_advising_request(teacher=teacher)
    AdvisingRequest.objects.filter(pk=answered.pk).update(status=AdvisingRequestStatus.REJECTED)
    other = make_advising_request()

    deactivate_user(teacher.user)

    statuses = dict(AdvisingRequest.objects.values_list("pk", "status"))
    assert statuses[first.pk] == AdvisingRequestStatus.UNANSWERED
    assert statuses[second.pk] == AdvisingRequestStatus.UNANSWERED
    assert statuses[answered.pk] == AdvisingRequestStatus.REJECTED
    assert statuses[other.pk] == AdvisingRequestStatus.PENDING
    assert not AdvisingRequest.objects.filter(
        teacher=teacher, status=AdvisingRequestStatus.PENDING
    ).exists()


def test_a_user_without_bonds_is_only_deactivated():
    student = make_student()
    teacher = make_teacher()
    bystander = make_advising()

    deactivate_user(student.user)
    deactivate_user(teacher.user)

    assert not User.objects.get(pk=student.user.pk).is_active
    assert not User.objects.get(pk=teacher.user.pk).is_active
    bystander.refresh_from_db()
    assert bystander.status == AdvisingStatus.ACTIVE


def test_the_other_party_and_the_other_advisings_are_untouched():
    teacher = make_teacher()
    mine = make_open_advising(teacher=teacher)
    unrelated = make_open_advising()

    deactivate_user(mine.student.user)

    assert User.objects.get(pk=teacher.user.pk).is_active
    unrelated.refresh_from_db()
    assert unrelated.status == AdvisingStatus.ACTIVE
    assert Meeting.objects.filter(advising=unrelated, status=MeetingStatus.CANCELLED).count() == 0
    assert Activity.objects.filter(advising=unrelated, status=ActivityStatus.ARCHIVED).count() == 0


def test_the_active_sessions_end_and_the_login_is_refused(browser):
    teacher = make_teacher()
    refresh = RefreshToken.for_user(teacher.user)
    other_refresh = RefreshToken.for_user(make_teacher().user)

    deactivate_user(teacher.user)

    outstanding = OutstandingToken.objects.get(jti=refresh["jti"])
    assert BlacklistedToken.objects.filter(token=outstanding).exists()
    assert not BlacklistedToken.objects.filter(token__jti=other_refresh["jti"]).exists()
    response = post_json(
        browser, LOGIN_URL, {"email": teacher.user.email, "password": DEFAULT_PASSWORD}
    )
    assert response.status_code == 401


def test_an_access_token_issued_before_the_deactivation_stops_working(client):
    student = make_student()
    headers = bearer(student.user)
    assert client.get(ME_URL, headers=headers).status_code == 200

    deactivate_user(student.user)

    assert client.get(ME_URL, headers=headers).status_code == 401


def test_the_email_of_a_deactivated_user_stays_taken():
    student = make_student()

    deactivate_user(student.user)

    assert User.objects.filter(email=student.user.email).count() == 1
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user(
            email=student.user.email,
            password=DEFAULT_PASSWORD,
            full_name="Outra Pessoa",
            type="student",
        )


def test_deactivating_twice_is_refused_and_changes_nothing():
    student = make_student()
    deactivate_user(student.user)

    with pytest.raises(ConflictError) as error:
        deactivate_user(student.user)

    assert error.value.detail == ALREADY_DEACTIVATED_MESSAGE


def test_a_failure_in_the_middle_of_the_cascade_undoes_everything():
    teacher = make_teacher()
    first = make_open_advising(teacher=teacher)
    second = make_open_advising(teacher=teacher)
    pending = make_advising_request(teacher=teacher)
    refresh = RefreshToken.for_user(teacher.user)
    calls = []

    def fail_on_the_second(advising, reason):
        calls.append(advising.pk)
        if len(calls) == 2:
            raise RuntimeError("falha no meio da cascata")
        return close_advising(advising, reason)

    with (
        patch("apps.accounts.deactivation.close_advising", side_effect=fail_on_the_second),
        pytest.raises(RuntimeError),
    ):
        deactivate_user(teacher.user)

    assert len(calls) == 2
    assert User.objects.get(pk=teacher.user.pk).is_active
    for advising in (first, second):
        advising.refresh_from_db()
        assert advising.status == AdvisingStatus.ACTIVE
        assert advising.end_reason == ""
        assert (
            Activity.objects.filter(advising=advising, status=ActivityStatus.ARCHIVED).count() == 0
        )
        assert (
            Meeting.objects.filter(advising=advising, status=MeetingStatus.CANCELLED).count() == 0
        )
    pending.refresh_from_db()
    assert pending.status == AdvisingRequestStatus.PENDING
    assert not BlacklistedToken.objects.filter(token__jti=refresh["jti"]).exists()


def test_a_request_made_after_the_deactivation_is_refused(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)

    deactivate_user(teacher.user)

    response = post_json(
        client,
        "/api/orientacao/solicitacoes",
        {"teacher_id": teacher.user_id},
        headers=bearer(student.user),
    )
    assert response.status_code == 404
    assert not AdvisingRequest.objects.filter(teacher=teacher).exists()
