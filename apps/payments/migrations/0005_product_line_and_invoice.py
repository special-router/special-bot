# Generated manually

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0004_transaction_from_referral_user'),
        ('users', '0004_alter_telegramuser_username'),
    ]

    operations = [
        migrations.AddField(
            model_name='transaction',
            name='product_line',
            field=models.CharField(
                choices=[('VPN_KEYS', 'VPN ключи'), ('SUBSCRIPTION', 'Подписки')],
                default='VPN_KEYS',
                max_length=15,
                verbose_name='Продукт',
            ),
        ),
        migrations.AddField(
            model_name='transaction',
            name='payment_method',
            field=models.CharField(
                blank=True,
                choices=[('TELEGRAM', 'Telegram'), ('CRYPTOBOT', 'CryptoBot')],
                default='',
                max_length=10,
                verbose_name='Способ оплаты',
            ),
        ),
        migrations.AlterField(
            model_name='transaction',
            name='source',
            field=models.CharField(
                choices=[
                    ('YOUMONEY', 'Юмани'),
                    ('CRYPTOBOT', 'CryptoBot'),
                    ('PROMO', 'Промо-баланс'),
                    ('EVERYDAY_SYSTEM', 'Ежедневное списание'),
                    ('BUY', 'Покупка'),
                    ('MANUAL', 'Руками проставили'),
                    ('REFERRAL', 'Реферальная система'),
                ],
                default='MANUAL',
                max_length=15,
                verbose_name='Источник',
            ),
        ),
        migrations.AddField(
            model_name='invoice',
            name='external_id',
            field=models.CharField(
                blank=True,
                max_length=255,
                null=True,
                unique=True,
                verbose_name='Внешний ID',
            ),
        ),
        migrations.AddField(
            model_name='invoice',
            name='product_line',
            field=models.CharField(
                choices=[('VPN_KEYS', 'VPN ключи'), ('SUBSCRIPTION', 'Подписки')],
                default='VPN_KEYS',
                max_length=15,
                verbose_name='Продукт',
            ),
        ),
        migrations.AddField(
            model_name='invoice',
            name='amount',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10, verbose_name='Сумма'),
        ),
        migrations.AddField(
            model_name='invoice',
            name='currency',
            field=models.CharField(default='RUB', max_length=10, verbose_name='Валюта'),
        ),
        migrations.AddField(
            model_name='invoice',
            name='status',
            field=models.CharField(
                choices=[('PENDING', 'Ожидание'), ('PAID', 'Оплачен'), ('EXPIRED', 'Истёк')],
                default='PENDING',
                max_length=10,
                verbose_name='Статус',
            ),
        ),
        migrations.AddField(
            model_name='invoice',
            name='payment_method',
            field=models.CharField(
                choices=[('TELEGRAM', 'Telegram'), ('CRYPTOBOT', 'CryptoBot')],
                default='TELEGRAM',
                max_length=10,
                verbose_name='Способ оплаты',
            ),
        ),
        migrations.AddField(
            model_name='invoice',
            name='payload',
            field=models.JSONField(blank=True, default=dict, verbose_name='Payload'),
        ),
        migrations.AddField(
            model_name='invoice',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, verbose_name='Время создания'),
        ),
    ]
