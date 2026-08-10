from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vpn', '0003_alter_uservpn_unique_together'),
    ]

    operations = [
        migrations.AddField(
            model_name='uservpn',
            name='sub_id',
            field=models.CharField(
                blank=True,
                db_index=True,
                default='',
                max_length=64,
                verbose_name='3x-ui subscription id',
            ),
        ),
    ]