from django.db import migrations

LEGACY_REASON = "Encerrada antes do registro de motivos."


def fill_legacy_reasons(apps, schema_editor):
    advising = apps.get_model("advising", "Advising")
    advising.objects.filter(status="ended", end_reason="").update(end_reason=LEGACY_REASON)


class Migration(migrations.Migration):
    dependencies = [
        ("advising", "0002_advising_end_reason"),
    ]

    operations = [
        migrations.RunPython(fill_legacy_reasons, migrations.RunPython.noop),
    ]
