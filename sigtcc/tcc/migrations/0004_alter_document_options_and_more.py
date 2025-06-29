# Generated by Django 5.2.1 on 2025-05-26 19:43

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('tcc', '0003_historicalsupervisionrequest_supervisionrequest'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='document',
            options={'verbose_name': 'Documento', 'verbose_name_plural': 'Documentos'},
        ),
        migrations.AlterModelOptions(
            name='historicalsupervisionrequest',
            options={'get_latest_by': ('history_date', 'history_id'), 'ordering': ('-history_date', '-history_id'), 'verbose_name': 'Histórico', 'verbose_name_plural': 'historical Solicitações de Supervisão'},
        ),
        migrations.AlterModelOptions(
            name='historicaltcc',
            options={'get_latest_by': ('history_date', 'history_id'), 'ordering': ('-history_date', '-history_id'), 'verbose_name': 'Histórico', 'verbose_name_plural': 'historical TCCs'},
        ),
        migrations.AlterModelOptions(
            name='supervisionrequest',
            options={'verbose_name': 'Solicitação de Supervisão', 'verbose_name_plural': 'Solicitações de Supervisão'},
        ),
        migrations.AlterModelOptions(
            name='tcc',
            options={'verbose_name': 'TCC', 'verbose_name_plural': 'TCCs'},
        ),
    ]
