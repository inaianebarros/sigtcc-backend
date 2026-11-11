from django.db import migrations, models
from django.utils import timezone


def settle_existing_notifications(apps, schema_editor):
    """The notifications that already exist were never meant to be e-mailed (ADR 0011): stamp them
    as settled, so turning the feature on does not mail the whole history."""
    Notification = apps.get_model("notifications", "Notification")
    Notification.objects.filter(email_sent_at__isnull=True).update(email_sent_at=timezone.now())


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0002_payload_and_new_types"),
    ]

    operations = [
        migrations.AddField(
            model_name="notification",
            name="email_attempts",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="notification",
            name="email_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(settle_existing_notifications, migrations.RunPython.noop),
    ]
