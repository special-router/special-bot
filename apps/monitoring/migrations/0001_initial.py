# Generated manually for the initial monitoring state schema.

from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='MonitorState',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('layer', models.CharField(max_length=8, unique=True)),
                ('last_ok', models.BooleanField(default=True)),
                ('consecutive_failures', models.PositiveIntegerField(default=0)),
                ('alert', models.BooleanField(default=False)),
                ('error_class', models.CharField(blank=True, max_length=64)),
                ('details', models.JSONField(blank=True, default=dict)),
                ('checked_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Monitoring state',
                'verbose_name_plural': 'Monitoring states',
            },
        ),
        migrations.CreateModel(
            name='MonitorTransition',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('layer', models.CharField(max_length=8)),
                ('event', models.CharField(max_length=16)),
                ('error_class', models.CharField(blank=True, max_length=64)),
                ('consecutive_failures', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'Monitoring transition',
                'verbose_name_plural': 'Monitoring transitions',
                'ordering': ('-created_at',),
            },
        ),
    ]
