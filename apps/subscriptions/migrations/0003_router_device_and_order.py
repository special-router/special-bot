# Generated manually

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('subscriptions', '0002_subscriptionplan'),
        ('users', '0004_alter_telegramuser_username'),
        ('vpn', '0005_uservpn_product_line_valid_until'),
    ]

    operations = [
        migrations.CreateModel(
            name='RouterDevice',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('serial_number', models.CharField(max_length=64, unique=True, verbose_name='Серийный номер')),
                ('display_id', models.CharField(help_text='Например: SPM 00001', max_length=32, verbose_name='ID устройства')),
                ('valid_until', models.DateTimeField(blank=True, null=True, verbose_name='Подписка до')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('activated_at', models.DateTimeField(blank=True, null=True)),
                (
                    'owner',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='router_devices',
                        to='users.telegramuser',
                    ),
                ),
                (
                    'user_vpn',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='router_device',
                        to='vpn.uservpn',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Устройство Special Router',
                'verbose_name_plural': 'Устройства Special Router',
            },
        ),
        migrations.CreateModel(
            name='RouterOrder',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                (
                    'order_type',
                    models.CharField(
                        choices=[
                            ('ROUTER_PURCHASE', 'Покупка роутера'),
                            ('SUBSCRIPTION', 'Продление подписки'),
                            ('ACTIVATION', 'Первая оплата при активации'),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    'status',
                    models.CharField(
                        choices=[
                            ('PENDING', 'Ожидание'),
                            ('PAID', 'Оплачен'),
                            ('SHIPPING', 'Ожидает доставку'),
                            ('COMPLETED', 'Завершён'),
                        ],
                        default='PENDING',
                        max_length=20,
                    ),
                ),
                ('amount', models.DecimalField(decimal_places=2, max_digits=10)),
                ('currency', models.CharField(default='RUB', max_length=8)),
                ('months', models.PositiveSmallIntegerField(default=0)),
                ('shipping_data', models.JSONField(blank=True, default=dict)),
                ('payment_payload', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                (
                    'device',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='orders',
                        to='subscriptions.routerdevice',
                    ),
                ),
                (
                    'user',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='router_orders',
                        to='users.telegramuser',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Заказ роутера',
                'verbose_name_plural': 'Заказы роутера',
            },
        ),
    ]
