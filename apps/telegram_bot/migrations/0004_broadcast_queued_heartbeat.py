from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('telegram_bot', '0003_broadcast_audience_broadcastdelivery')]

    operations = [
        migrations.AlterField(
            model_name='broadcast',
            name='status',
            field=models.CharField(
                choices=[
                    ('draft', 'Черновик'), ('queued', 'В очереди'),
                    ('sending', 'Отправляется'), ('sent', 'Отправлено'), ('failed', 'Ошибка'),
                ],
                default='draft', max_length=20, verbose_name='Статус',
            ),
        ),
        migrations.AddField(
            model_name='broadcast',
            name='heartbeat_at',
            field=models.DateTimeField(
                blank=True, null=True, verbose_name='Последняя активность доставки',
                help_text='Обновляется задачей доставки и используется для безопасного восстановления.',
            ),
        ),
    ]
