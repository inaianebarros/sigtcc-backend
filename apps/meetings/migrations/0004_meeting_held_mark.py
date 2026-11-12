from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("meetings", "0003_meeting_ends_at"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="meeting",
            name="held_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="meeting",
            name="held_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.PROTECT,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddConstraint(
            model_name="meeting",
            constraint=models.CheckConstraint(
                condition=models.Q(("held_at__isnull", True), ("held_by__isnull", True))
                | models.Q(("held_at__isnull", False), ("held_by__isnull", False)),
                name="meeting_held_mark_has_moment_and_author",
            ),
        ),
        migrations.AddConstraint(
            model_name="meeting",
            constraint=models.CheckConstraint(
                condition=models.Q(("held_at__isnull", True), ("status", "accepted"), _connector="OR"),
                name="meeting_held_mark_requires_accepted",
            ),
        ),
    ]
