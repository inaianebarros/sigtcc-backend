"""The migration that adds the end of the meeting fills the meetings that already exist with one
hour after the start, so none is left without it (RN45, ADR 0016, #174)."""

import datetime

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from tests.factories import make_meeting

BEFORE = [("meetings", "0002_meeting_cancellation_reason")]
AFTER = [("meetings", "0003_meeting_ends_at")]


@pytest.mark.django_db(transaction=True)
def test_the_meetings_that_already_exist_end_one_hour_after_they_start():
    start = timezone.now() + datetime.timedelta(days=3)
    meeting = make_meeting(scheduled_at=start)
    other = make_meeting(scheduled_at=start + datetime.timedelta(days=1, minutes=17))

    try:
        MigrationExecutor(connection).migrate(BEFORE)
        executor = MigrationExecutor(connection)
        executor.migrate(AFTER)
        Meeting = executor.loader.project_state(AFTER).apps.get_model("meetings", "Meeting")

        for row in (meeting, other):
            migrated = Meeting.objects.get(pk=row.pk)
            assert migrated.ends_at == migrated.scheduled_at + datetime.timedelta(hours=1)
        assert Meeting.objects.filter(ends_at__isnull=True).count() == 0
    finally:
        MigrationExecutor(connection).migrate(
            MigrationExecutor(connection).loader.graph.leaf_nodes()
        )
