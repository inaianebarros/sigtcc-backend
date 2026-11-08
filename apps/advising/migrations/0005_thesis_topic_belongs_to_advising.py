import django.db.models.deletion
from django.db import migrations, models


def attach_topics_to_advisings(apps, schema_editor):
    """Moves each topic to the active advising of its student, else to the latest one.

    A topic of a student with no advising has no owner anymore and is dropped.
    """
    ThesisTopic = apps.get_model("advising", "ThesisTopic")
    Advising = apps.get_model("advising", "Advising")
    for topic in ThesisTopic.objects.all():
        advising = (
            Advising.objects.filter(student_id=topic.student_id)
            .order_by(models.Case(models.When(status="active", then=0), default=1), "-id")
            .first()
        )
        if advising is None:
            topic.delete()
        else:
            topic.advising = advising
            topic.save(update_fields=["advising"])


class Migration(migrations.Migration):
    dependencies = [
        ("advising", "0004_advising_end_reason_constraint"),
    ]

    operations = [
        migrations.AddField(
            model_name="thesistopic",
            name="advising",
            field=models.OneToOneField(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="thesis_topic",
                to="advising.advising",
            ),
        ),
        migrations.RunPython(attach_topics_to_advisings),
        migrations.RemoveField(model_name="thesistopic", name="student"),
        migrations.AlterField(
            model_name="thesistopic",
            name="advising",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="thesis_topic",
                to="advising.advising",
            ),
        ),
    ]
