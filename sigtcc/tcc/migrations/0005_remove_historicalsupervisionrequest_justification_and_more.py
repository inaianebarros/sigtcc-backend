# Generated by Django 5.2.3 on 2025-06-25 23:55

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tcc', '0004_alter_document_options_and_more'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='historicalsupervisionrequest',
            name='justification',
        ),
        migrations.RemoveField(
            model_name='supervisionrequest',
            name='justification',
        ),
        migrations.AddField(
            model_name='historicalsupervisionrequest',
            name='professor_message',
            field=models.TextField(default=None, max_length=1500, verbose_name='Mensagem do Professor'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='historicalsupervisionrequest',
            name='student_message',
            field=models.TextField(default=None, max_length=1500, verbose_name='Mensagem do Aluno'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='supervisionrequest',
            name='professor_message',
            field=models.TextField(default=None, max_length=1500, verbose_name='Mensagem do Professor'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='supervisionrequest',
            name='student_message',
            field=models.TextField(default=None, max_length=1500, verbose_name='Mensagem do Aluno'),
            preserve_default=False,
        ),
    ]
