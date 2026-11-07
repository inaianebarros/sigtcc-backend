from django.contrib.postgres.operations import UnaccentExtension
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_unique_published_articles"),
    ]

    operations = [
        UnaccentExtension(),
    ]
