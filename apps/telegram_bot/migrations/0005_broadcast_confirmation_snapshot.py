import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('telegram_bot', '0004_broadcast_queued_heartbeat')]

    operations = [
        migrations.AlterField(
            model_name='broadcast',
            name='status',
            field=models.CharField(
                choices=[
                    ('draft', 'Черновик'), ('confirming', 'Ожидает подтверждения'),
                    ('queued', 'В очереди'), ('sending', 'Отправляется'),
                    ('sent', 'Отправлено'), ('failed', 'Ошибка'),
                ],
                default='draft', max_length=20, verbose_name='Статус',
            ),
        ),
        migrations.AddField(
            model_name='broadcast',
            name='preview_snapshot_id',
            field=models.UUIDField(
                blank=True, editable=False,
                help_text='Создаётся для неизменяемого снимка получателей перед подтверждением.',
                null=True, verbose_name='Идентификатор снимка подтверждения',
            ),
        ),
    ]
