import django.db.models.deletion
from django.core.validators import MaxLengthValidator, MinLengthValidator
from django.db import migrations, models


def preserve_legacy_audience(apps, schema_editor):
    # Existing broadcasts predate audience selection and must retain their former
    # all-users meaning rather than being silently narrowed by the new default.
    apps.get_model('telegram_bot', 'Broadcast').objects.update(audience='all')


class Migration(migrations.Migration):

    dependencies = [
        ('telegram_bot', '0002_broadcast_photo'),
        ('users', '0004_alter_telegramuser_username'),
    ]

    operations = [
        migrations.AddField(
            model_name='broadcast',
            name='audience',
            field=models.CharField(
                choices=[
                    ('subscription_ready', 'Владельцы готовых оплаченных подписок'),
                    ('all', 'Все пользователи'),
                ],
                default='all',
                help_text='Получатели фиксируются при первом запуске отправки.',
                max_length=32,
                verbose_name='Аудитория',
            ),
        ),
        migrations.RunPython(preserve_legacy_audience, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='broadcast',
            name='audience',
            field=models.CharField(
                choices=[
                    ('subscription_ready', 'Владельцы готовых оплаченных подписок'),
                    ('all', 'Все пользователи'),
                ],
                default='subscription_ready',
                help_text='Получатели фиксируются при первом запуске отправки.',
                max_length=32,
                verbose_name='Аудитория',
            ),
        ),
        migrations.AddField(
            model_name='broadcast',
            name='include_subscription_button',
            field=models.BooleanField(
                default=False,
                help_text='Добавляет приватную кнопку «Открыть мою подписку» без ссылки в сообщении.',
                verbose_name='Добавить кнопку подписки',
            ),
        ),
        migrations.CreateModel(
            name='BroadcastDelivery',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('pending', 'Ожидает'), ('sending', 'Отправляется'), ('sent', 'Отправлено'), ('failed', 'Ошибка')], default='pending', max_length=16)),
                ('error_class', models.CharField(blank=True, max_length=64)),
                ('attempt_count', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('sending_at', models.DateTimeField(blank=True, null=True)),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('broadcast', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='deliveries', to='telegram_bot.broadcast')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='broadcast_deliveries', to='users.telegramuser')),
            ],
        ),
        migrations.AddConstraint(
            model_name='broadcastdelivery',
            constraint=models.UniqueConstraint(fields=('broadcast', 'user'), name='unique_broadcast_delivery'),
        ),
        migrations.AddIndex(
            model_name='broadcastdelivery',
            index=models.Index(fields=['broadcast', 'status'], name='telegram_bo_broadca_b98dc2_idx'),
        ),
        migrations.AlterField(
            model_name='broadcast',
            name='message',
            field=models.TextField(
                help_text='Текст сообщения для рассылки',
                validators=[MinLengthValidator(10), MaxLengthValidator(4096)],
                verbose_name='Сообщение',
            ),
        ),
        migrations.AlterField(
            model_name='broadcast',
            name='scheduled_at',
            field=models.DateTimeField(
                blank=True,
                help_text='Историческое значение; не используется для запуска рассылки.',
                null=True,
                verbose_name='Запланировано на',
            ),
        ),
    ]
