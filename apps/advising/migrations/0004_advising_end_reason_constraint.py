from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("advising", "0003_fill_legacy_end_reasons"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="advising",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("status", "ended"), models.Q(("end_reason", ""), _negated=True)),
                    models.Q(("end_reason", ""), ("status", "active")),
                    _connector="OR",
                ),
                name="advising_end_reason_matches_status",
            ),
        ),
    ]
