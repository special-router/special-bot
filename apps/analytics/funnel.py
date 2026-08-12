"""API воронки: по одной функции на шаг, с указанием места вызова.

Ключи идемпотентности строятся из того, что уже есть в месте вызова, чтобы
повторный апдейт Telegram не удваивал шаг. Ничего секретного в них не попадает:
только внутренние идентификаторы, шаг и дата.

Все функции синхронные и ходят в базу, а обработчики бота асинхронные: место
вызова обязано обернуть их в ``sync_to_async``, иначе Django поднимет
``SynchronousOnlyOperation``, а перехват внутри ``record_funnel_event`` молча
его проглотит — шаг не запишется, и никто этого не заметит.

Где вызывается (файл → функция → шаг):

``apps/telegram_bot/handlers/balance.py``
    ``show_balance`` после отрисовки → ``balance_screen_shown``
``apps/telegram_bot/handlers/top_up_balance.py``
    ``top_up_balance_promo`` после создания начисления → ``promo_claimed``
    ``top_up_balance_days`` до ``send_invoice`` → ``topup_plan_chosen``
    ``top_up_balance_days`` после ``send_invoice`` → ``invoice_sent``
    ``pre_checkout_callback`` после ``query.answer(ok=True)`` →
        ``pre_checkout_approved``
    ``successful_payment_callback`` после создания строки пополнения →
        ``payment_completed`` и ``record_topup`` из ``apps.analytics.recording``:
        второй уточняет выведенную из лестницы бонусов сумму до измеренной.
``apps/telegram_bot/handlers/add_key.py``
    ``add_key`` в ветке нехватки баланса → ``subscription_refused_no_funds``
    ``add_key`` после ``add_vpn_to_user`` → ``subscription_created``
``apps/telegram_bot/handlers/remove_key.py``
    ``remove_key`` после удаления → ``subscription_removed``
``apps/subscriptions/tasks.py``
    ``SUBSCRIPTION_DISABLED_NO_FUNDS``: он рождается не в кнопке, а в биллинге.

Счёт и всё, что за ним, остаются нулевыми, пока не задан ``YOUMONEY_TOKEN``:
кнопки сумм при пустом токене скрыты. Это не пропуск в подключении, а сам
предмет измерения — до провайдера доходят, дальше не идут.
"""
from __future__ import annotations

import datetime
import hashlib
from decimal import Decimal

from django.utils import timezone

from apps.analytics.choices import FunnelStepChoices
from apps.analytics.recording import record_funnel_event


def _day_key(step: str, user_id: int, moment: datetime.datetime | None = None) -> str:
    """Ключ «один шаг на пользователя в сутки»: экраны нажимают многократно."""
    day = (moment or timezone.now()).astimezone(datetime.timezone.utc).date()
    return f'fn:{step}:{user_id}:{day.isoformat()}'


def balance_screen_shown(user_id: int) -> None:
    record_funnel_event(
        user_id,
        FunnelStepChoices.BALANCE_SCREEN_SHOWN,
        event_key=_day_key(FunnelStepChoices.BALANCE_SCREEN_SHOWN, user_id),
    )


def promo_claimed(user_id: int) -> None:
    # Промо выдаётся не больше одного раза за всё время — ключ без даты.
    record_funnel_event(
        user_id,
        FunnelStepChoices.PROMO_CLAIMED,
        event_key=f'fn:{FunnelStepChoices.PROMO_CLAIMED}:{user_id}',
    )


def topup_plan_chosen(user_id: int, *, amount: Decimal, days: int) -> None:
    record_funnel_event(
        user_id,
        FunnelStepChoices.TOPUP_PLAN_CHOSEN,
        event_key=f'{_day_key(FunnelStepChoices.TOPUP_PLAN_CHOSEN, user_id)}:{days}',
        amount=amount,
        days=days,
    )


def invoice_sent(user_id: int, *, amount: Decimal, days: int) -> None:
    record_funnel_event(
        user_id,
        FunnelStepChoices.INVOICE_SENT,
        event_key=f'{_day_key(FunnelStepChoices.INVOICE_SENT, user_id)}:{days}',
        amount=amount,
        days=days,
    )


def pre_checkout_approved(user_id: int, *, amount: Decimal) -> None:
    record_funnel_event(
        user_id,
        FunnelStepChoices.PRE_CHECKOUT_APPROVED,
        event_key=f'{_day_key(FunnelStepChoices.PRE_CHECKOUT_APPROVED, user_id)}:{amount}',
        amount=amount,
    )


def payment_completed(user_id: int, *, amount: Decimal, charge_id: str) -> None:
    """``charge_id`` только хешируется в ключ: сам идентификатор платежа не хранится.

    Хеш именно blake2s, а не ``hash()``: встроенный хеш строк рандомизируется на
    каждый запуск процесса, и ключ перестал бы совпадать после перезапуска.
    """
    digest = hashlib.blake2s(charge_id.encode('utf-8'), digest_size=8).hexdigest()
    record_funnel_event(
        user_id,
        FunnelStepChoices.PAYMENT_COMPLETED,
        event_key=f'fn:{FunnelStepChoices.PAYMENT_COMPLETED}:{user_id}:{digest}',
        amount=amount,
    )


def subscription_created(user_id: int, user_vpn_id: int) -> None:
    record_funnel_event(
        user_id,
        FunnelStepChoices.SUBSCRIPTION_CREATED,
        event_key=f'fn:{FunnelStepChoices.SUBSCRIPTION_CREATED}:{user_vpn_id}',
        user_vpn_id=user_vpn_id,
    )


def subscription_refused_no_funds(user_id: int, *, amount: Decimal) -> None:
    record_funnel_event(
        user_id,
        FunnelStepChoices.SUBSCRIPTION_REFUSED_NO_FUNDS,
        event_key=_day_key(FunnelStepChoices.SUBSCRIPTION_REFUSED_NO_FUNDS, user_id),
        amount=amount,
    )


def subscription_removed(user_id: int, user_vpn_id: int) -> None:
    record_funnel_event(
        user_id,
        FunnelStepChoices.SUBSCRIPTION_REMOVED,
        event_key=f'fn:{FunnelStepChoices.SUBSCRIPTION_REMOVED}:{user_vpn_id}',
        user_vpn_id=user_vpn_id,
    )


def subscription_disabled_no_funds(user_id: int, user_vpn_id: int, charge_date: datetime.date) -> None:
    """Подключено в биллинге. Ключ — подписка и сутки прогона, как у списания."""
    record_funnel_event(
        user_id,
        FunnelStepChoices.SUBSCRIPTION_DISABLED_NO_FUNDS,
        event_key=f'fn:disabled:{user_vpn_id}:{charge_date.isoformat()}',
        user_vpn_id=user_vpn_id,
    )
