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
    )

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.PROTECT,
        related_name='transactions',
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
            )
        ]

    def __str__(self):
        return f"{self.user.username} - {self.amount} - {self.status}"
