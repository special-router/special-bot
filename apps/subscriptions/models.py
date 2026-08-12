from django.db import models
from django.utils import timezone


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


class SubscriptionDevice(models.Model):
    """One client device bound to a subscription by its Happ ``x-hwid`` value.

    Every metadata field is filled from client-supplied headers, so each one is
    length-capped at the column level rather than trusted.
    """

    subscription = models.ForeignKey(
        'vpn.UserVPN',
        on_delete=models.CASCADE,
        related_name='devices',
    )

    hwid = models.CharField(
        'Идентификатор устройства',
        max_length=64,
    )

    device_os = models.CharField(
        'ОС устройства',
        max_length=32,
        blank=True,
        default='',
    )

    os_version = models.CharField(
        'Версия ОС',
        max_length=32,
        blank=True,
        default='',
    )

    device_model = models.CharField(
        'Модель устройства',
        max_length=64,
        blank=True,
        default='',
    )

    user_agent = models.CharField(
        'User-Agent',
        max_length=128,
        blank=True,
        default='',
    )

    first_seen_at = models.DateTimeField(
        'Первое обращение',
        auto_now_add=True,
    )

    last_seen_at = models.DateTimeField(
        'Последнее обращение',
        default=timezone.now,
    )

    class Meta:
        verbose_name = 'Устройство подписки'
        verbose_name_plural = 'Устройства подписок'
        constraints = [
            models.UniqueConstraint(
                fields=['subscription', 'hwid'],
                name='unique_subscription_device',
            ),
        ]

    def __str__(self):
        return f"{self.subscription_id} {self.device_model or self.device_os}"


class SubscriptionDeviceReset(models.Model):
    """Last time a user cleared their bound devices, for the self-serve cooldown."""

    telegram_user = models.OneToOneField(
        'users.TelegramUser',
        on_delete=models.CASCADE,
        related_name='device_reset',
    )

    last_reset_at = models.DateTimeField(
        'Последний сброс устройств',
        default=timezone.now,
    )

    class Meta:
        verbose_name = 'Сброс устройств'
        verbose_name_plural = 'Сбросы устройств'

    def __str__(self):
        return f"{self.telegram_user} {str(self.last_reset_at)}"
