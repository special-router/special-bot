# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vpn', '0004_uservpn_vless_links'),
    ]

    operations = [
        migrations.AddField(
            model_name='uservpn',
            name='product_line',
            field=models.CharField(
                choices=[('VPN_KEYS', 'VPN ключи'), ('SUBSCRIPTION', 'Подписки')],
                default='VPN_KEYS',
                max_length=15,
                verbose_name='Продукт',
            ),
        ),
        migrations.AddField(
            model_name='uservpn',
            name='valid_until',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Действительно до'),
        ),
    ]
