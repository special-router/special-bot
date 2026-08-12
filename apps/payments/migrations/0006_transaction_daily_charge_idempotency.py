# Generated manually for daily-charge idempotency.
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0005_compensationgrant_transaction_source'),
        ('vpn', '0004_uservpn_sub_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='transaction',
            name='user_vpn',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='transactions',
                to='vpn.uservpn',
            ),
        ),
        migrations.AddField(
            model_name='transaction',
            name='charge_date',
            field=models.DateField(
                blank=True,
                null=True,
                verbose_name='Дата ежедневного списания',
            ),
        ),
        # Существующие строки остаются с NULL в обоих полях: NULL не участвует в
        # уникальном индексе ни в PostgreSQL, ни в SQLite, поэтому бэкфилл не нужен.
        migrations.AddConstraint(
            model_name='transaction',
            constraint=models.UniqueConstraint(
                condition=models.Q(source='EVERYDAY_SYSTEM'),
                fields=('user_vpn', 'charge_date'),
                name='unique_everyday_charge_per_subscription_day',
            ),
        ),
    ]
