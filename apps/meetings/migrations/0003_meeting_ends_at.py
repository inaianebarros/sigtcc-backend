import datetime

from django.db import migrations, models
from django.db.models import F


def fill_ends_at(apps, schema_editor):
    """The meetings that already exist end one hour after they start (ADR backend 0016)."""
    apps.get_model("meetings", "Meeting").objects.update(
        ends_at=F("scheduled_at") + datetime.timedelta(hours=1)
    )


class Migration(migrations.Migration):

    dependencies = [
        ("meetings", "0002_meeting_cancellation_reason"),
    ]

    operations = [
        migrations.AddField(
            model_name="meeting",
            name="ends_at",
            field=models.DateTimeField(null=True),
        ),
        migrations.RunPython(fill_ends_at, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="meeting",
            name="ends_at",
            field=models.DateTimeField(),
        ),
        migrations.AddConstraint(
            model_name="meeting",
            constraint=models.CheckConstraint(
                condition=models.Q(("ends_at__gt", models.F("scheduled_at"))),
                name="meeting_ends_after_it_starts",
            ),
        ),
    ]
