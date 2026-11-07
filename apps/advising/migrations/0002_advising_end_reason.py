from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_unaccent_extension"),
        ("advising", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="advising",
            name="end_reason",
            field=models.TextField(blank=True, default=""),
        ),
    ]
