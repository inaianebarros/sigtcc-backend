import datetime

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.meetings.models import Meeting, MeetingStatus
from apps.notifications.models import Notification, NotificationType
from tests.factories import make_advising

pytestmark = pytest.mark.django_db


def schedule_meeting(**extra):
    advising = make_advising()
    return Meeting.objects.create(
        advising=advising,
        author=advising.student.user,
        scheduled_at=timezone.now(),
        ends_at=timezone.now() + datetime.timedelta(hours=1),
        description="Alinhar o capítulo 2",
        **extra,
    )


def test_new_meeting_starts_pending():
    assert schedule_meeting().status == MeetingStatus.PENDING


def test_rejected_meeting_requires_a_reason():
    with pytest.raises(IntegrityError), transaction.atomic():
        schedule_meeting(status=MeetingStatus.REJECTED)

    meeting = schedule_meeting(status=MeetingStatus.REJECTED, rejection_reason="Estou em viagem")

    assert meeting.rejection_reason == "Estou em viagem"


def test_a_meeting_cancelled_by_the_system_needs_the_cancelled_status_and_a_reason():
    with pytest.raises(IntegrityError), transaction.atomic():
        schedule_meeting(status=MeetingStatus.CANCELLED, cancelled_by_system=True)
    with pytest.raises(IntegrityError), transaction.atomic():
        schedule_meeting(
            status=MeetingStatus.ACCEPTED, cancelled_by_system=True, cancellation_reason="Motivo"
        )

    meeting = schedule_meeting(
        status=MeetingStatus.CANCELLED, cancelled_by_system=True, cancellation_reason="Motivo"
    )

    assert meeting.cancelled_by_system is True


def test_a_cancellation_reason_only_exists_on_a_cancelled_meeting():
    with pytest.raises(IntegrityError), transaction.atomic():
        schedule_meeting(status=MeetingStatus.ACCEPTED, cancellation_reason="Motivo")

    by_the_author = schedule_meeting(
        status=MeetingStatus.CANCELLED, cancellation_reason="Tive um imprevisto"
    )
    without_reason = schedule_meeting(status=MeetingStatus.CANCELLED)

    assert by_the_author.cancelled_by_system is False
    assert without_reason.cancellation_reason == ""


def test_notification_starts_unread_and_references_any_object():
    advising = make_advising()

    notification = Notification.objects.create(
        recipient=advising.teacher.user,
        type=NotificationType.ADVISING_REQUEST_RECEIVED,
        reference_type="advising_request",
        reference_id=42,
    )

    assert notification.is_read is False
    assert notification.created_at is not None
