from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('vpn', '0005_uservpn_device_limit'),
    ]

    operations = [
        migrations.AddField(
            model_name='uservpn',
            name='device_billing_exempt',
            field=models.BooleanField(
                default=False,
                verbose_name='Дополнительные устройства без доплаты',
            ),
        ),
    ]
