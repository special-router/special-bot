import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0006_transaction_daily_charge_idempotency'),
        ('users', '0001_initial'),
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
                    ('CRYPTO', 'Криптовалюта'),
                ],
                default='MANUAL',
                max_length=15,
                verbose_name='Источник',
            ),
        ),
        migrations.CreateModel(
            name='CryptoBotInvoice',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('invoice_id', models.BigIntegerField(unique=True)),
                ('amount_rub', models.DecimalField(decimal_places=2, max_digits=12)),
                ('amount_usdt', models.DecimalField(decimal_places=6, max_digits=12)),
                ('paid', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='cryptobot_invoices',
                    to='users.telegramuser',
                )),
            ],
        ),
    ]
