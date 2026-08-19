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


class SubscriptionDeviceBindingWindow(models.Model):
    """When a user last asked, from the bot, to bind another device.

    The subscription endpoint is public, so the moment a new device may be
    registered has to come from the authenticated side.  This row is that
    consent: it is written only by a Telegram callback, whose sender identity
    is signed by Telegram, and it expires on its own.
    """

    telegram_user = models.OneToOneField(
        'users.TelegramUser',
        on_delete=models.CASCADE,
        related_name='device_binding_window',
    )

    opened_at = models.DateTimeField(
        'Окно привязки открыто',
        default=timezone.now,
    )

    class Meta:
        verbose_name = 'Окно привязки устройств'
        verbose_name_plural = 'Окна привязки устройств'

    def __str__(self):
        return f"{self.telegram_user} {str(self.opened_at)}"


class MirrorEndpointLiveness(models.Model):
    """Whether one third-party endpoint completed a real handshake, and when.

    Rows are written out of band by ``probe_mirror_liveness`` and only read by
    the subscription renderer, which never dials anything itself: a request has
    an eight-second fetch deadline and a handshake takes seconds per node.

    A row is a measurement rather than a state, which is why ``checked_at`` is
    stored instead of a flag with a lifetime.  A server that was down an hour
    ago may be up now, so the reader decides how old a verdict may be, and a
    prober that stops running degrades to the blind selection this deployment
    shipped before liveness existed rather than to an empty list.
    """

    host = models.CharField(
        'Адрес эндпоинта',
        max_length=253,
    )

    port = models.PositiveIntegerField(
        'Порт',
    )

    alive = models.BooleanField(
        'Отвечает',
        default=False,
    )

    error_class = models.CharField(
        'Класс отказа',
        max_length=64,
        blank=True,
        default='',
    )

    checked_at = models.DateTimeField(
        'Проверено',
        default=timezone.now,
    )

    probed_from = models.CharField(
        'Источник замера',
        max_length=32,
        blank=True,
        default='',
    )

    class Meta:
        verbose_name = 'Живость зеркального эндпоинта'
        verbose_name_plural = 'Живость зеркальных эндпоинтов'
        constraints = [
            models.UniqueConstraint(
                fields=['host', 'port'],
                name='unique_mirror_endpoint_liveness',
            ),
        ]

    def __str__(self):
        return f"{self.host}:{self.port} {'alive' if self.alive else 'dead'}"


class SubscriptionDeviceRegistrationRate(models.Model):
    """Rolling count of new device registrations for one subscription.

    Slots freed by a reset must not become a fresh flooding budget, so this
    counter survives the devices it counted and bounds registrations even while
    a binding window is legitimately open.
    """

    subscription = models.OneToOneField(
        'vpn.UserVPN',
        on_delete=models.CASCADE,
        related_name='device_registration_rate',
    )

    period_started_at = models.DateTimeField(
        'Начало периода',
        default=timezone.now,
    )

    registrations = models.PositiveIntegerField(
        'Регистраций за период',
        default=0,
    )

    class Meta:
        verbose_name = 'Частота привязок подписки'
        verbose_name_plural = 'Частоты привязок подписок'

    def __str__(self):
        return f"{self.subscription_id} {self.registrations}"
