from django.db import models

from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.payments.querysets import TransactionQuerySet


class Invoice(models.Model):
    user = models.ForeignKey(
        'users.TelegramUser',
        on_delete=models.PROTECT,
        related_name='invoices',
    )


class Transaction(models.Model):
    objects = TransactionQuerySet.as_manager()

    user = models.ForeignKey(
        'users.TelegramUser',
        on_delete=models.PROTECT,
        related_name='transactions',
    )

    amount = models.DecimalField(
        'Сумма',
        max_digits=10,
        decimal_places=2,
    )

    status = models.CharField(
        'Статус',
        max_length=7,
        choices=TransactionStatusChoices,
    )

    created_at = models.DateTimeField(
        'Время создания',
        auto_now_add=True,
    )

    source = models.CharField(
        'Источник',
        max_length=15,
        choices=TransactionSourceChoices,
        default=TransactionSourceChoices.MANUAL,
    )

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.PROTECT,
        related_name='transactions',
        blank=True,
        null=True,
    )

    from_referral_user = models.ForeignKey(
        'users.TelegramUser',
        on_delete=models.PROTECT,
        related_name='referral_transactions',
        blank=True,
        null=True,
    )

    user_vpn = models.ForeignKey(
        'vpn.UserVPN',
        on_delete=models.SET_NULL,
        related_name='transactions',
        blank=True,
        null=True,
    )

    # Отдельное поле, а не выражение над created_at: функциональный индекс по дате из
    # timestamptz не является IMMUTABLE в PostgreSQL и не может быть проиндексирован.
    charge_date = models.DateField(
        'Дата ежедневного списания',
        blank=True,
        null=True,
    )

    # todo: добавить тип, комментарий, источник, ссылка на сервер

    class Meta:
        verbose_name = 'Транзакция'
        verbose_name_plural = 'Транзакции'
        constraints = [
            models.UniqueConstraint(
                fields=['user'],
                condition=models.Q(source='PROMO'),
                name='unique_promo_transaction_per_user',
            ),
            models.UniqueConstraint(
                fields=['user_vpn', 'charge_date'],
                condition=models.Q(source='EVERYDAY_SYSTEM'),
                name='unique_everyday_charge_per_subscription_day',
            ),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.amount} - {self.status}"


class CompensationGrant(models.Model):
    """One auditable grant per user for each named outage campaign."""

    campaign = models.SlugField(max_length=64)
    user = models.ForeignKey(
        'users.TelegramUser',
        on_delete=models.PROTECT,
        related_name='compensation_grants',
    )
    amount = models.DecimalField('Сумма', max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Компенсационное начисление'
        verbose_name_plural = 'Компенсационные начисления'
        constraints = [
            models.UniqueConstraint(
                fields=['campaign', 'user'],
                name='unique_compensation_grant_per_campaign_user',
            ),
        ]

    def __str__(self):
        return f'{self.campaign} - {self.user_id} - {self.amount}'
