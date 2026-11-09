"""The notification of the end of an advising (RF53): one, to the student, with the counts."""

import datetime
from unittest.mock import patch

import pytest
from django.test import Client
from django.utils import timezone

from apps.activities.models import ActivityStatus
from apps.advising.models import Advising, AdvisingStatus
from apps.meetings.models import MeetingStatus
from apps.notifications.models import Notification
from tests.api.helpers import CREATE_NOTIFICATION, bearer, inbox, post_json
from tests.factories import (
    make_activity,
    make_advising,
    make_course,
    make_meeting,
    make_student,
    make_teacher,
)

pytestmark = pytest.mark.django_db

REASON = "Concluímos o ciclo de orientação do TCC."


def advised_pair():
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    return student, teacher, make_advising(student=student, teacher=teacher)


def end(client, teacher, advising):
    return post_json(
        client,
        f"/api/orientacao/{advising.id}/encerrar",
        {"reason": REASON},
        headers=bearer(teacher.user),
    )


def add_activity(advising, status):
    activity = make_activity(advising)
    activity.status = status
    activity.save(update_fields=["status"])
    return activity


def in_days(days):
    return timezone.now() + datetime.timedelta(days=days)


def test_ending_notifies_only_the_student_once_with_the_counts(client):
    student, teacher, advising = advised_pair()
    for status in (ActivityStatus.PENDING, ActivityStatus.IN_PROGRESS, ActivityStatus.IN_REVIEW):
        add_activity(advising, status)
    add_activity(advising, ActivityStatus.COMPLETED)
    make_meeting(advising, MeetingStatus.PENDING, in_days(3))
    make_meeting(advising, MeetingStatus.ACCEPTED, in_days(4))

    response = end(client, teacher, advising)

    assert response.status_code == 200
    (item,) = inbox(client, student.user)
    assert item["type"] == "advising_ended"
    assert item["reference"] == {"type": "advising", "id": advising.id}
    assert item["is_read"] is False
    assert item["payload"] == {
        "teacher_name": teacher.user.full_name,
        "archived_activities": 3,
        "cancelled_meetings": 2,
    }
    assert item["message"] == (
        f"{teacher.user.full_name} encerrou a sua orientação. "
        "3 atividades em aberto foram arquivadas. "
        "2 reuniões pendentes ou aceitas foram canceladas."
    )
    assert inbox(client, teacher.user) == []
    assert Notification.objects.count() == 1


def test_the_counts_only_include_what_the_ending_really_changed(client):
    student, teacher, advising = advised_pair()
    add_activity(advising, ActivityStatus.PENDING)
    add_activity(advising, ActivityStatus.COMPLETED)
    deleted = add_activity(advising, ActivityStatus.PENDING)
    deleted.deleted_at = timezone.now()
    deleted.save(update_fields=["deleted_at"])
    make_meeting(advising, MeetingStatus.ACCEPTED, in_days(2))
    make_meeting(advising, MeetingStatus.ACCEPTED, in_days(-2))
    make_meeting(advising, MeetingStatus.REJECTED, in_days(2))
    make_meeting(advising, MeetingStatus.CANCELLED, in_days(2))

    end(client, teacher, advising)

    (item,) = inbox(client, student.user)
    assert item["payload"]["archived_activities"] == 1
    assert item["payload"]["cancelled_meetings"] == 1


def test_one_of_each_uses_the_singular(client):
    student, teacher, advising = advised_pair()
    add_activity(advising, ActivityStatus.IN_PROGRESS)
    make_meeting(advising, MeetingStatus.PENDING, in_days(3))

    end(client, teacher, advising)

    (item,) = inbox(client, student.user)
    assert item["message"] == (
        f"{teacher.user.full_name} encerrou a sua orientação. "
        "1 atividade em aberto foi arquivada. "
        "1 reunião pendente ou aceita foi cancelada."
    )


def test_with_nothing_to_archive_or_cancel_the_message_says_so(client):
    student, teacher, advising = advised_pair()

    end(client, teacher, advising)

    (item,) = inbox(client, student.user)
    assert item["payload"]["archived_activities"] == 0
    assert item["payload"]["cancelled_meetings"] == 0
    assert item["message"] == (
        f"{teacher.user.full_name} encerrou a sua orientação. "
        "Nenhuma atividade em aberto foi arquivada. "
        "Nenhuma reunião pendente ou aceita foi cancelada."
    )


def test_an_ending_that_fails_notifies_nobody(client):
    student, teacher, advising = advised_pair()
    other_teacher = make_teacher(courses=[make_course()])

    other = end(client, other_teacher, advising)
    first = end(client, teacher, advising)
    Notification.objects.all().delete()
    again = end(client, teacher, advising)

    assert other.status_code == 404
    assert first.status_code == 200
    assert again.status_code == 409
    assert not Notification.objects.exists()


def test_a_failing_notification_rolls_the_ending_back():
    student, teacher, advising = advised_pair()
    activity = add_activity(advising, ActivityStatus.PENDING)
    meeting = make_meeting(advising, MeetingStatus.PENDING, in_days(3))
    quiet_client = Client(raise_request_exception=False)

    with patch(CREATE_NOTIFICATION, side_effect=RuntimeError("boom")):
        response = end(quiet_client, teacher, advising)

    assert response.status_code == 500
    assert Advising.objects.get(pk=advising.pk).status == AdvisingStatus.ACTIVE
    activity.refresh_from_db()
    meeting.refresh_from_db()
    assert activity.status == ActivityStatus.PENDING
    assert meeting.status == MeetingStatus.PENDING
    assert not Notification.objects.exists()
