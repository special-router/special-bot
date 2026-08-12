"""Запись аналитических событий. Ни одна ошибка здесь не касается денег.

**Почему после коммита, а не в той же транзакции.** Событие пишется из
``transaction.on_commit`` и любое исключение внутри гасится в лог. Общая
транзакция дала бы согласованность, но ценой того, что отказ на аналитической
вставке откатывал бы списание, платёж или компенсацию. Обмен несимметричный:
потерянное событие восстанавливается — ``Transaction`` остаётся источником
правды, а бэкфилл идемпотентен и допишет пропуск с тем же ключом; несписанные
или неначисленные деньги не восстанавливает ничто. В режиме autocommit
``on_commit`` выполняется сразу, поэтому обработчик, у которого своей транзакции
нет, всё равно получает запись немедленно.
"""
from __future__ import annotations

import datetime
import logging
from decimal import Decimal

from django.conf import settings
from django.db import transaction as db_transaction
from django.utils import timezone

from apps.analytics.choices import CashBasisChoices, DateBasisChoices, EventOriginChoices
from apps.analytics.models import FunnelEvent, MoneyEvent
from apps.analytics.taxonomy import classify
from apps.payments.models import Transaction


logger = logging.getLogger(__name__)

# Поля, которые пересчитываются при повторной классификации: всё остальное —
# обстоятельства события и меняться не может.
_CLASSIFIED_FIELDS = (
    'kind',
    'economic_class',
    'cash_basis',
    'balance_delta',
    'cash_amount',
    'revenue_amount',
    'credit_amount',
    'payout_amount',
)


def analytics_enabled() -> bool:
    return getattr(settings, 'ANALYTICS_EVENTS_ENABLED', True)


def money_event_key(transaction_id: int) -> str:
    return f'tx:{transaction_id}'


def effective_date_of(transaction: Transaction) -> tuple[datetime.date, str]:
    """Дата, к которой отчёт относит строку, и откуда она взята.

    У ежедневного списания есть ``charge_date`` — сутки, за которые списали. У
    строк старше этого поля он пуст, и остаётся только момент создания: прогон
    биллинга идёт в 00:00 UTC, поэтому дата создания совпадает с оплаченными
    сутками везде, кроме старта в последние минуты предыдущего дня.
    """
    if transaction.charge_date is not None:
        return transaction.charge_date, DateBasisChoices.CHARGE_DATE
    created_at = transaction.created_at or timezone.now()
    return created_at.astimezone(datetime.timezone.utc).date(), DateBasisChoices.CREATED_AT


def record_money_event(
    transaction: Transaction,
    *,
    measured_cash: Decimal | None = None,
    origin: str = EventOriginChoices.LIVE,
    refresh: bool = False,
) -> MoneyEvent | None:
    """Записать (или дописать) событие для денежной строки. Идемпотентно.

    ``measured_cash`` — сумма, реально списанная с карты; её знает только место
    вызова платежа. Событие, уже записанное с выведенной суммой, обновляется до
    измеренной: это единственное исключение из «только добавление», и оно уточняет
    оценку фактом, а не переписывает прошлое. ``refresh`` пересчитывает
    классификацию существующего события после изменения таксономии.
    """
    classification = classify(transaction.source, transaction.amount, measured_cash=measured_cash)
    effective_date, date_basis = effective_date_of(transaction)

    event, created = MoneyEvent.objects.get_or_create(
        event_key=money_event_key(transaction.pk),
        defaults={
            'occurred_at': transaction.created_at or timezone.now(),
            'effective_date': effective_date,
            'origin': origin,
            'user_id': transaction.user_id,
            'transaction_id': transaction.pk,
            'user_vpn_id': transaction.user_vpn_id,
            'referred_user_id': transaction.from_referral_user_id,
            'source': transaction.source,
            'status': transaction.status,
            'kind': classification.kind,
            'economic_class': classification.economic_class,
            'cash_basis': classification.cash_basis,
            'date_basis': date_basis,
            'balance_delta': classification.balance_delta,
            'cash_amount': classification.cash_amount,
            'revenue_amount': classification.revenue_amount,
            'credit_amount': classification.credit_amount,
            'payout_amount': classification.payout_amount,
        },
    )
    if created:
        return event

    upgrades_cash = (
        classification.cash_basis == CashBasisChoices.MEASURED and event.cash_basis != CashBasisChoices.MEASURED
    )
    if not (refresh or upgrades_cash):
        return event

    for field in _CLASSIFIED_FIELDS:
        setattr(event, field, getattr(classification, field))
    event.status = transaction.status
    event.effective_date = effective_date
    event.date_basis = date_basis
    event.save(update_fields=[*_CLASSIFIED_FIELDS, 'status', 'effective_date', 'date_basis'])
    return event


def record_funnel_event(
    user_id: int,
    step: str,
    *,
    event_key: str | None = None,
    occurred_at: datetime.datetime | None = None,
    user_vpn_id: int | None = None,
    amount: Decimal | None = None,
    days: int | None = None,
    origin: str = EventOriginChoices.LIVE,
) -> FunnelEvent | None:
    """Записать шаг воронки. Идемпотентно по ``event_key``, никогда не бросает.

    Место вызова, которое может повториться (повтор апдейта Telegram, ретрай
    задачи), обязано передать устойчивый ``event_key``. Без него ключ строится из
    шага, пользователя и секунды события — этого хватает, чтобы двойное нажатие
    не удвоило шаг, но не хватает при повторе через минуту.
    """
    if not analytics_enabled():
        return None
    occurred_at = occurred_at or timezone.now()
    key = event_key or f'fn:{step}:{user_id}:{int(occurred_at.timestamp())}'
    try:
        event, _ = FunnelEvent.objects.get_or_create(
            event_key=key,
            defaults={
                'occurred_at': occurred_at,
                'effective_date': occurred_at.astimezone(datetime.timezone.utc).date(),
                'origin': origin,
                'user_id': user_id,
                'user_vpn_id': user_vpn_id,
                'step': step,
                'amount': amount,
                'days': days,
            },
        )
        return event
    except Exception:
        logger.exception('analytics funnel event %s for user %s not recorded', step, user_id)
        return None


def record_topup(transaction: Transaction, *, cash_amount: Decimal) -> MoneyEvent | None:
    """Уточнить пополнение измеренной суммой платежа. Не бросает.

    Вызывается из обработчика успешного платежа, который единственный видит
    ``payment.total_amount``. Сигнал к этому моменту уже записал событие с суммой,
    выведенной из лестницы бонусов; этот вызов заменяет оценку фактом.
    """
    if not analytics_enabled():
        return None
    try:
        return record_money_event(transaction, measured_cash=cash_amount)
    except Exception:
        logger.exception('analytics topup refinement for transaction %s not recorded', transaction.pk)
        return None


def schedule_money_event(transaction: Transaction, *, measured_cash: Decimal | None = None) -> None:
    """Поставить запись события на момент после коммита денежной строки."""
    if not analytics_enabled():
        return
    db_transaction.on_commit(lambda: _record_quietly(transaction, measured_cash))


def schedule_funnel_event(user_id: int, step: str, **kwargs) -> None:
    """То же для шага воронки: после коммита и без права уронить вызвавший код."""
    if not analytics_enabled():
        return
    db_transaction.on_commit(lambda: record_funnel_event(user_id, step, **kwargs))


def _record_quietly(transaction: Transaction, measured_cash: Decimal | None) -> None:
    try:
        record_money_event(transaction, measured_cash=measured_cash)
    except Exception:
        # Строка денег уже зафиксирована; событие допишет следующий прогон
        # бэкфилла по тому же ключу.
        logger.exception('analytics money event for transaction %s not recorded', transaction.pk)
