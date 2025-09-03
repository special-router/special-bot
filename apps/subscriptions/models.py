from django.db import models


class Subscription(models.Model):
    telegram_user = models.ForeignKey(
        'users.TelegramUser',
        on_delete=models.PROTECT,
        related_name='subscriptions',
    )

    url = models.URLField('VPN url')

    server = models.ForeignKey(
        'servers.Server',
        on_delete=models.PROTECT,
        related_name='subscriptions',
    )

    valid_until = models.DateTimeField(
        'Действительно до',
    )

    class Meta:
        verbose_name = 'Подписка'
        verbose_name_plural = 'Подписки'

    def __str__(self):
        return f"{self.telegram_user} {str(self.valid_until)}"
