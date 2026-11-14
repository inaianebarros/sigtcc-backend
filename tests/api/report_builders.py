"""Builders of an ended advising and of its records, shared by the tests of the report (RF61)."""

from datetime import date, timedelta

from django.utils import timezone

from apps.activities.models import Activity, ActivityStatus
from apps.advising.models import Advising, AdvisingStatus, ThesisTopic
from apps.meetings.models import Meeting, MeetingStatus
from tests.factories import make_advising

REASON = "Concluímos o ciclo de orientação do TCC."


def ended_advising(student=None, teacher=None, topic=None, reason=REASON):
    advising = make_advising(student=student, teacher=teacher)
    if topic is not None:
        ThesisTopic.objects.create(advising=advising, description=topic)
    Advising.objects.filter(pk=advising.pk).update(
        status=AdvisingStatus.ENDED, ended_at=timezone.now(), end_reason=reason
    )
    advising.refresh_from_db()
    return advising


def add_meeting(
    advising,
    status=MeetingStatus.ACCEPTED,
    days_from_now=-3,
    held=False,
    at=None,
    description="Reunião de acompanhamento",
    notes="",
):
    start = at or timezone.now() + timedelta(days=days_from_now)
    return Meeting.objects.create(
        advising=advising,
        author=advising.teacher.user,
        scheduled_at=start,
        ends_at=start + timedelta(hours=1),
        description=description,
        notes=notes,
        status=status,
        rejection_reason="Sem horário." if status == MeetingStatus.REJECTED else "",
        cancellation_reason="Imprevisto." if status == MeetingStatus.CANCELLED else "",
        held_at=timezone.now() if held else None,
        held_by=advising.teacher.user if held else None,
    )


def add_activity(
    advising,
    status=ActivityStatus.COMPLETED,
    deleted=False,
    title="Revisar capítulo",
    description="Entregar o capítulo",
    deadline=date(2026, 11, 20),
):
    return Activity.objects.create(
        advising=advising,
        title=title,
        description=description,
        deadline=deadline,
        status=status,
        deleted_at=timezone.now() if deleted else None,
    )
