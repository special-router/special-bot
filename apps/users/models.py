from django.db import models
from apps.users.querysets import TelegramUserQuerySet

class TelegramUser(models.Model):

    objects = TelegramUserQuerySet.as_manager()

    telegram_id = models.BigIntegerField(
        'Telegram ID',
    )

    username = models.TextField(
        'Telegram Username',
        blank=True,
    )

    referral_user = models.ForeignKey(
        'TelegramUser',
        on_delete=models.PROTECT,
        related_name='servers',
        null=True,
        blank=True,
    )

    is_active_promo = models.BooleanField(
        'Был активирован промо',
        default=False,
    )

    # auto updated fields
    updated_at = models.DateTimeField(
        'Время обновления записи',
        auto_now=True,
    )

    created_at = models.DateTimeField(
        'Время создания записи',
        auto_now_add=True,
    )

    class Meta:
        verbose_name = 'Telegram User'
        verbose_name_plural = 'Telegram Users'

    def __str__(self):
        return f'{self.username} {self.telegram_id}'