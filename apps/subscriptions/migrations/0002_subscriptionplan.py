# Generated manually

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('subscriptions', '0001_initial'),
        ('servers', '0003_server_subscription_inbounds'),
    ]

    operations = [
        migrations.CreateModel(
            name='SubscriptionPlan',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255, verbose_name='Наименование')),
                (
                    'monthly_price',
                    models.DecimalField(decimal_places=2, max_digits=10, verbose_name='Цена за месяц, руб'),
                ),
                ('is_active', models.BooleanField(default=True, verbose_name='Активен')),
                (
                    'server',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='subscription_plans',
                        to='servers.server',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Тариф подписки',
                'verbose_name_plural': 'Тарифы подписок',
            },
        ),
    ]
