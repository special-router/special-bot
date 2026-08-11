# Generated manually for outage-compensation campaign idempotency.
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0004_transaction_from_referral_user'),
    ]

    operations = [
        migrations.AlterField(
            model_name='transaction',
            name='source',
            field=models.CharField(
                choices=[
                    ('YOUMONEY', 'Юмани'),
                    ('PROMO', 'Промо-баланс'),
                    ('EVERYDAY_SYSTEM', 'Ежедневное списание'),
                    ('BUY', 'Покупка'),
                    ('MANUAL', 'Руками проставили'),
                    ('REFERRAL', 'Реферальная система'),
                    ('COMPENSATION', 'Компенсация простоя'),
                ],
                default='MANUAL',
                max_length=15,
                verbose_name='Источник',
            ),
        ),
        migrations.CreateModel(
            name='CompensationGrant',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('campaign', models.SlugField(max_length=64)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=10, verbose_name='Сумма')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='compensation_grants', to='users.telegramuser')),
            ],
            options={
                'verbose_name': 'Компенсационное начисление',
                'verbose_name_plural': 'Компенсационные начисления',
                'constraints': [
                    models.UniqueConstraint(fields=('campaign', 'user'), name='unique_compensation_grant_per_campaign_user'),
                ],
            },
        ),
    ]
