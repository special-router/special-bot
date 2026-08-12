from django.db import models

from apps.analytics.choices import (
    CashBasisChoices,
    DateBasisChoices,
    EconomicClassChoices,
    EventOriginChoices,
    FunnelStepChoices,
    MoneyEventKindChoices,
)


class MoneyEvent(models.Model):
    """Аналитическая проекция одной денежной строки. Баланс считается не отсюда.

    Пишется только добавлением. ``event_key`` уникален и для строк из
    ``Transaction`` равен ``tx:<id>``, поэтому повтор записи, повтор задачи и
    повторный прогон бэкфилла не могут посчитать одно событие дважды. Ровно та же
    причина позволяет бэкфиллу дописывать то, что живой путь не успел записать:
    ключи совпадут.
    """

    event_key = models.CharField('Ключ идемпотентности', max_length=128, unique=True)
    occurred_at = models.DateTimeField('Время события', db_index=True)
    effective_date = models.DateField('Дата, к которой относится событие')
    recorded_at = models.DateTimeField('Время записи', auto_now_add=True)
    origin = models.CharField(max_length=8, choices=EventOriginChoices, default=EventOriginChoices.LIVE)

    user = models.ForeignKey(
        'users.TelegramUser',
        on_delete=models.PROTECT,
        related_name='money_events',
    )
    transaction = models.ForeignKey(
        'payments.Transaction',
        on_delete=models.SET_NULL,
        related_name='money_events',
        blank=True,
        null=True,
    )
    user_vpn = models.ForeignKey(
        'vpn.UserVPN',
        on_delete=models.SET_NULL,
        related_name='money_events',
        blank=True,
        null=True,
    )
    # Кто из приглашённых породил выплату: без этого нельзя сопоставить выплаты
    # реферальной программы с деньгами приглашённых.
    referred_user = models.ForeignKey(
        'users.TelegramUser',
        on_delete=models.SET_NULL,
        related_name='referral_money_events',
        blank=True,
        null=True,
    )

    source = models.CharField('Источник строки', max_length=15)
    status = models.CharField('Статус строки', max_length=7)
    kind = models.CharField(max_length=32, choices=MoneyEventKindChoices)
    economic_class = models.CharField(max_length=16, choices=EconomicClassChoices)
    cash_basis = models.CharField(max_length=8, choices=CashBasisChoices)
    date_basis = models.CharField(max_length=12, choices=DateBasisChoices)

    balance_delta = models.DecimalField('Влияние на баланс', max_digits=12, decimal_places=2)
    cash_amount = models.DecimalField('Полученные деньги', max_digits=12, decimal_places=2, default=0)
    revenue_amount = models.DecimalField('Признанная выручка', max_digits=12, decimal_places=2, default=0)
    credit_amount = models.DecimalField('Выданный баланс', max_digits=12, decimal_places=2, default=0)
    payout_amount = models.DecimalField('Выплата партнёру', max_digits=12, decimal_places=2, default=0)

    class Meta:
        verbose_name = 'Денежное событие'
        verbose_name_plural = 'Денежные события'
        indexes = [
            models.Index(fields=['effective_date', 'economic_class'], name='money_event_date_class_idx'),
            models.Index(fields=['user', 'effective_date'], name='money_event_user_date_idx'),
            models.Index(fields=['kind', 'effective_date'], name='money_event_kind_date_idx'),
        ]

    def __str__(self):
        return f'{self.effective_date} {self.kind} {self.balance_delta}'


class FunnelEvent(models.Model):
    """Шаг пути клиента: экран, счёт, платёж, отключение за неуплату.

    Отдельная модель, потому что у шага нет денежной суммы и он не обязан быть
    связан со строкой в ``Transaction``. Идемпотентность — тем же ``event_key``.
    """

    event_key = models.CharField('Ключ идемпотентности', max_length=128, unique=True)
    occurred_at = models.DateTimeField('Время события', db_index=True)
    effective_date = models.DateField('Дата, к которой относится событие')
    recorded_at = models.DateTimeField('Время записи', auto_now_add=True)
    origin = models.CharField(max_length=8, choices=EventOriginChoices, default=EventOriginChoices.LIVE)

    user = models.ForeignKey(
        'users.TelegramUser',
        on_delete=models.PROTECT,
        related_name='funnel_events',
    )
    user_vpn = models.ForeignKey(
        'vpn.UserVPN',
        on_delete=models.SET_NULL,
        related_name='funnel_events',
        blank=True,
        null=True,
    )

    step = models.CharField(max_length=40, choices=FunnelStepChoices)
    # Выбранная пользователем сумма и срок: без них воронка отвечает «сколько
    # дошло», но не «на каком ценнике отваливаются».
    amount = models.DecimalField('Сумма шага', max_digits=12, decimal_places=2, blank=True, null=True)
    days = models.PositiveIntegerField('Срок шага в днях', blank=True, null=True)

    class Meta:
        verbose_name = 'Событие воронки'
        verbose_name_plural = 'События воронки'
        indexes = [
            models.Index(fields=['effective_date', 'step'], name='funnel_event_date_step_idx'),
            models.Index(fields=['user', 'effective_date'], name='funnel_event_user_date_idx'),
        ]

    def __str__(self):
        return f'{self.effective_date} {self.step}'
