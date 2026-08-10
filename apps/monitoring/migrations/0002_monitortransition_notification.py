from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('monitoring', '0001_initial')]

    operations = [
        migrations.AddField(
            model_name='monitortransition',
            name='notification_attempted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='monitortransition',
            name='notification_delivered',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='monitortransition',
            name='notification_error_class',
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name='monitortransition',
            name='notification_destination_owner',
            field=models.CharField(blank=True, max_length=64),
        ),
    ]
