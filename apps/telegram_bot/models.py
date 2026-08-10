from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator, MinLengthValidator
from django.db import models
from django.db.models import Count, DecimalField, Exists, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from apps.payments.choices import TransactionStatusChoices
from apps.payments.models import Transaction
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


class Broadcast(models.Model):
    """A Telegram broadcast with a snapshotted, auditable audience."""

    STATUS_CHOICES = [
        ('draft', 'Черновик'),
        ('confirming', 'Ожидает подтверждения'),
        ('queued', 'В очереди'),
        ('sending', 'Отправляется'),
        ('sent', 'Отправлено'),
        ('failed', 'Ошибка'),
    ]
    AUDIENCE_ALL = 'all'
    AUDIENCE_SUBSCRIPTION_READY = 'subscription_ready'
    AUDIENCE_CHOICES = [
        (AUDIENCE_SUBSCRIPTION_READY, 'Владельцы готовых оплаченных подписок'),
        (AUDIENCE_ALL, 'Все пользователи'),
    ]

    title = models.CharField('Заголовок', max_length=200, help_text='Краткое описание рассылки')
    message = models.TextField(
        'Сообщение',
        help_text='Текст сообщения для рассылки',
        validators=[MinLengthValidator(10), MaxLengthValidator(4096)],
    )
    audience = models.CharField(
        'Аудитория',
        max_length=32,
        choices=AUDIENCE_CHOICES,
        default=AUDIENCE_SUBSCRIPTION_READY,
        help_text='Получатели фиксируются при первом запуске отправки.',
    )
    include_subscription_button = models.BooleanField(
        'Добавить кнопку подписки',
        default=False,
        help_text='Добавляет приватную кнопку «Открыть мою подписку» без ссылки в сообщении.',
    )
    status = models.CharField('Статус', max_length=20, choices=STATUS_CHOICES, default='draft')
    created_by = models.ForeignKey(
        User, on_delete=models.CASCADE, verbose_name='Создано пользователем', related_name='broadcasts'
    )
    total_users = models.PositiveIntegerField(
        'Всего пользователей', default=0, help_text='Общее количество пользователей для рассылки'
    )
    sent_count = models.PositiveIntegerField('Отправлено', default=0, help_text='Количество успешно отправленных сообщений')
    failed_count = models.PositiveIntegerField('Ошибок', default=0, help_text='Количество неудачных отправок')
    error_message = models.TextField(
        'Сообщение об ошибке', blank=True, null=True, help_text='Детали ошибки при отправке'
    )
    # Retained as historical data; broadcasts are always explicitly confirmed before enqueueing.
    scheduled_at = models.DateTimeField(
        'Запланировано на',
        null=True,
        blank=True,
        help_text='Историческое значение; не используется для запуска рассылки.',
    )
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлено', auto_now=True)
    preview_snapshot_id = models.UUIDField(
        'Идентификатор снимка подтверждения', null=True, blank=True, editable=False,
        help_text='Создаётся для неизменяемого снимка получателей перед подтверждением.',
    )
    heartbeat_at = models.DateTimeField(
        'Последняя активность доставки', null=True, blank=True,
        help_text='Обновляется задачей доставки и используется для безопасного восстановления.',
    )
    sent_at = models.DateTimeField('Отправлено', null=True, blank=True, help_text='Время фактической отправки')
    photo = models.ImageField(
        'Фото',
        upload_to='broadcasts/',
        null=True,
        blank=True,
        help_text='Необязательно: изображение, которое будет отправлено вместе с текстом',
    )

    class Meta:
        verbose_name = 'Рассылка'
        verbose_name_plural = 'Рассылки'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"

    def clean(self):
        super().clean()
        if self.message and len(self.message.strip()) < 10:
            raise ValidationError({'message': 'Сообщение должно содержать минимум 10 символов'})

    @classmethod
    def canonical_recipients(cls):
        """One deterministic TelegramUser row per Telegram account."""
        earlier_duplicate = TelegramUser.objects.filter(
            telegram_id=OuterRef('telegram_id'), pk__lt=OuterRef('pk')
        )
        return TelegramUser.objects.annotate(
            has_earlier_duplicate=Exists(earlier_duplicate)
        ).filter(has_earlier_duplicate=False)

    @classmethod
    def subscription_ready_recipients(cls):
        """Canonical owners with one unique, affordable prepared UserVPN.

        Each predicate is evaluated against the same UserVPN row.  A reused nonempty
        subscription id is excluded rather than risking delivery to an ambiguous key.
        ``enabled`` is deliberately not used as an entitlement signal.
        """
        balance = (
            Transaction.objects.filter(
                user_id=OuterRef('pk'), status=TransactionStatusChoices.SUCCESS
            )
            .values('user_id')
            .annotate(total=Sum('amount'))
            .values('total')[:1]
        )
        duplicate_sub_ids = (
            UserVPN.objects.filter(sub_id__gt='')
            .values('sub_id')
            .annotate(row_count=Count('pk'))
            .filter(row_count__gt=1)
            .values('sub_id')
        )
        recipients = cls.canonical_recipients().annotate(
            entitlement_balance=Coalesce(
                Subquery(balance, output_field=DecimalField(max_digits=10, decimal_places=2)),
                Value(0),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        )
        prepared_vpn = (
            UserVPN.objects.filter(user_id=OuterRef('pk'), sub_id__gt='')
            .exclude(sub_id__in=Subquery(duplicate_sub_ids))
            .filter(server__tariff__price__lte=OuterRef('entitlement_balance'))
        )
        return recipients.annotate(has_prepared_subscription=Exists(prepared_vpn)).filter(
            has_prepared_subscription=True
        )

    def recipient_queryset(self):
        if self.audience == self.AUDIENCE_ALL:
            return self.canonical_recipients()
        return self.subscription_ready_recipients()

    @property
    def success_rate(self):
        if self.total_users == 0:
            return 0
        return round((self.sent_count / self.total_users) * 100, 2)

    def can_be_sent(self):
        """Only an explicitly queued broadcast can be claimed by a worker."""
        return self.status == 'queued'

    def is_sending(self):
        return self.status == 'sending'

    def is_completed(self):
        return self.status in ['sent', 'failed']


class BroadcastDelivery(models.Model):
    """One immutable recipient snapshot row and its delivery state."""

    STATUS_PENDING = 'pending'
    STATUS_SENDING = 'sending'
    STATUS_SENT = 'sent'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Ожидает'),
        (STATUS_SENDING, 'Отправляется'),
        (STATUS_SENT, 'Отправлено'),
        (STATUS_FAILED, 'Ошибка'),
    ]

    broadcast = models.ForeignKey(Broadcast, on_delete=models.CASCADE, related_name='deliveries')
    user = models.ForeignKey(TelegramUser, on_delete=models.PROTECT, related_name='broadcast_deliveries')
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    error_class = models.CharField(max_length=64, blank=True)
    attempt_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    sending_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['broadcast', 'user'], name='unique_broadcast_delivery')]
        indexes = [models.Index(fields=['broadcast', 'status'])]

    def __str__(self):
        return f'Broadcast delivery {self.broadcast_id}: {self.status}'
