"""The migration that adds the e-mail delivery state settles the notifications that already exist
(ADR 0011, #153): the history is never mailed."""

import pytest
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

BEFORE = [("notifications", "0002_payload_and_new_types")]
AFTER = [("notifications", "0003_email_delivery")]


@pytest.mark.django_db(transaction=True)
def test_the_notifications_that_already_exist_are_settled_and_the_new_ones_are_pending(mailoutbox):
    executor = MigrationExecutor(connection)
    executor.migrate(BEFORE)
    old_apps = executor.loader.project_state(BEFORE).apps
    User = old_apps.get_model("accounts", "User")
    OldNotification = old_apps.get_model("notifications", "Notification")
    user = User.objects.create(email="antigo@example.com", full_name="Antigo", type="student")
    old = OldNotification.objects.create(recipient=user, type="activity_created")

    try:
        executor = MigrationExecutor(connection)
        executor.migrate(AFTER)
        new_apps = executor.loader.project_state(AFTER).apps
        Notification = new_apps.get_model("notifications", "Notification")
        assert Notification.objects.get(pk=old.pk).email_sent_at is not None
        fresh = Notification.objects.create(recipient_id=user.pk, type="activity_created")
        assert fresh.email_sent_at is None
        assert fresh.email_attempts == 0

        call_command("send_notification_emails")

        assert len(mailoutbox) == 1
        assert mailoutbox[0].to == ["antigo@example.com"]
    finally:
        MigrationExecutor(connection).migrate(
            MigrationExecutor(connection).loader.graph.leaf_nodes()
        )
