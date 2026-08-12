"""Восстановление событий из существующих строк ``Transaction``.

Перезапускаемо и идемпотентно: ключ денежного события — это идентификатор строки,
поэтому повторный прогон ничего не удваивает и дописывает только то, чего нет.
Это же делает бэкфилл штатным средством восстановления после сбоя живой записи и
единственным источником событий для мест вызова, ещё не подключённых к API.

Что история восстановить не даёт:

* Ежедневные списания старше миграции ``0006`` не ссылаются на подписку и не
  имеют ``charge_date``. Их дата берётся из ``created_at`` (прогон идёт в 00:00
  UTC), а отвал виден только на уровне аккаунта — отсюда шаги
  ``ACCOUNT_BILLING_LAPSED`` и ``ACCOUNT_BILLING_RESUMED``, а не отключение
  конкретной подписки.
* Пропуск, после которого списания не возобновились, неотличим от подписки,
  удалённой самим пользователем: удаление в истории не записано.
* Начисления руками не говорят, принимал ли владелец деньги вне провайдера, —
  см. ``taxonomy._classify_manual``.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field

from django.utils import timezone

from apps.analytics.choices import EventOriginChoices, FunnelStepChoices
from apps.analytics.models import FunnelEvent, MoneyEvent
from apps.analytics.recording import money_event_key, record_funnel_event, record_money_event
from apps.payments.choices import TransactionSourceChoices
from apps.payments.models import Transaction


DAY = datetime.timedelta(days=1)


@dataclass
class BackfillResult:
    transactions_seen: int = 0
    money_events_created: int = 0
    money_events_refreshed: int = 0
    lapses_created: int = 0
    resumes_created: int = 0
    unknown_sources: dict[str, int] = field(default_factory=dict)

    def as_line(self) -> str:
        unknown = ','.join(f'{name}={count}' for name, count in sorted(self.unknown_sources.items())) or 'none'
        return (
            f'transactions_seen={self.transactions_seen} '
            f'money_events_created={self.money_events_created} '
            f'money_events_refreshed={self.money_events_refreshed} '
            f'lapses_created={self.lapses_created} '
            f'resumes_created={self.resumes_created} '
            f'unknown_sources={unknown}'
        )


def backfill_money_events(
    *,
    since: datetime.date | None = None,
    until: datetime.date | None = None,
    refresh: bool = False,
    batch_size: int = 500,
) -> BackfillResult:
    """Пройти строки транзакций и записать недостающие денежные события."""
    result = BackfillResult()
    known = set(TransactionSourceChoices.values)

    rows = Transaction.objects.order_by('id')
    if since is not None:
        rows = rows.filter(created_at__date__gte=since)
    if until is not None:
        rows = rows.filter(created_at__date__lte=until)

    existing_keys = set(MoneyEvent.objects.values_list('event_key', flat=True))

    for row in rows.iterator(chunk_size=batch_size):
        result.transactions_seen += 1
        if row.source not in known:
            result.unknown_sources[row.source] = result.unknown_sources.get(row.source, 0) + 1
        is_new = money_event_key(row.pk) not in existing_keys
        if not is_new and not refresh:
            continue
        record_money_event(row, origin=EventOriginChoices.BACKFILL, refresh=refresh)
        if is_new:
            result.money_events_created += 1
            existing_keys.add(money_event_key(row.pk))
        else:
            result.money_events_refreshed += 1

    return result


def backfill_billing_gaps(
    *,
    since: datetime.date | None = None,
    until: datetime.date | None = None,
    result: BackfillResult | None = None,
) -> BackfillResult:
    """Вывести отвалы и возвраты аккаунтов из ряда дат ежедневных списаний.

    Аккаунт, списанный в сутки N и не списанный в N+1, деньги в тот день не
    платил: биллинг либо отключил его подписки, либо их уже не было. Дальнейшее
    списание после пропуска — возврат. Это единственная форма оттока, которую
    старые строки позволяют увидеть, и она про аккаунт, а не про подписку.
    """
    result = result or BackfillResult()
    today = timezone.now().astimezone(datetime.timezone.utc).date()
    horizon = min(until, today) if until is not None else today

    charge_days: dict[int, set[datetime.date]] = {}
    rows = Transaction.objects.filter_by_source(TransactionSourceChoices.EVERYDAY_SYSTEM).values_list(
        'user_id', 'charge_date', 'created_at'
    )
    for user_id, charge_date, created_at in rows.iterator(chunk_size=2000):
        day = charge_date or created_at.astimezone(datetime.timezone.utc).date()
        charge_days.setdefault(user_id, set()).add(day)

    for user_id, days in charge_days.items():
        ordered = sorted(days)
        for previous, current in zip(ordered, ordered[1:]):
            if current - previous <= DAY:
                continue
            _emit_gap(user_id, previous + DAY, current, since, until, result)
        last = ordered[-1]
        if horizon - last > DAY:
            _emit_gap(user_id, last + DAY, None, since, until, result)

    return result


def _emit_gap(
    user_id: int,
    lapsed_on: datetime.date,
    resumed_on: datetime.date | None,
    since: datetime.date | None,
    until: datetime.date | None,
    result: BackfillResult,
) -> None:
    if _in_window(lapsed_on, since, until):
        if _record_day_step(user_id, FunnelStepChoices.ACCOUNT_BILLING_LAPSED, lapsed_on):
            result.lapses_created += 1
    if resumed_on is not None and _in_window(resumed_on, since, until):
        if _record_day_step(user_id, FunnelStepChoices.ACCOUNT_BILLING_RESUMED, resumed_on):
            result.resumes_created += 1


def _in_window(day: datetime.date, since: datetime.date | None, until: datetime.date | None) -> bool:
    return (since is None or day >= since) and (until is None or day <= until)


def _record_day_step(user_id: int, step: str, day: datetime.date) -> bool:
    key = f'fn:{step}:{user_id}:{day.isoformat()}'
    if FunnelEvent.objects.filter(event_key=key).exists():
        return False
    moment = datetime.datetime.combine(day, datetime.time.min, tzinfo=datetime.timezone.utc)
    return (
        record_funnel_event(
            user_id,
            step,
            event_key=key,
            occurred_at=moment,
            origin=EventOriginChoices.BACKFILL,
        )
        is not None
    )
