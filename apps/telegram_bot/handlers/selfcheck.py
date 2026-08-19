"""«Всё ли у меня в порядке»: то же самое, что уже знаем мы сами.

Клиент с нерабочим ключом сегодня может только написать в поддержку и ждать —
а подписка, устройства и наши точки уже видны нам самим. Экран не гадает и не
подменяет отсутствие данных оптимистичным дефолтом: там, где мы чего-то не
знаем, так и написано, без зелёной галочки.

Ни ссылка подписки, ни `vpn_uuid`, ни адрес какого-либо узла сюда не попадают —
это то же самое ограничение, что уже держит экран «Устройства».
"""
from __future__ import annotations

import html
from typing import Final

from asgiref.sync import sync_to_async
from django.utils import timezone
from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from apps.monitoring.models import MonitorState
from apps.subscriptions.devices import bound_devices, device_limit_for
from apps.subscriptions.pricing import daily_price
from apps.telegram_bot.handlers.devices import _subscription, device_display_name
from apps.telegram_bot.inline_buttons.selfcheck import get_reply_markup_selfcheck
from apps.telegram_bot.ui import bold, render_screen, screen
from apps.telegram_bot.utils import get_user
from apps.users.models import TelegramUser


NO_SUBSCRIPTION: Final[str] = 'Подписки нет — сначала подключите её на экране «Подписки».'

SUBSCRIPTION_DEAD: Final[str] = 'Подписка не работает. Пополните баланс, чтобы она снова заработала.'

NO_DEVICES: Final[str] = 'Устройств не привязано — подписка ещё не открывалась ни на одном.'

DEVICE_STALE_HINT: Final[str] = (
    ' Похоже, конфигурация не обновлялась давно — откройте приложение и обновите подписку вручную.'
)

# Устройство, обратившееся меньше суток назад, обновляется в своём обычном
# темпе; дальше это уже повод посоветовать обновить конфигурацию вручную.
DEVICE_STALE_AFTER = timezone.timedelta(hours=24)

NO_MONITORING: Final[str] = 'Мы пока не проверяем это автоматически.'

MONITOR_UNKNOWN: Final[str] = 'Мы пока не знаем текущее состояние — попробуйте позже или напишите в поддержку.'

# L1 гоняется раз в минуту (см. `docs/FLAGS.md`), поэтому запись старше десяти
# минут значит, что сам мониторинг, скорее всего, встал, а не что точки живы.
MONITOR_STALE_AFTER = timezone.timedelta(minutes=10)

ENDPOINT_DOWN_HINT: Final[str] = (
    'Один из путей подключения сейчас недоступен. Остальные должны работать — если у вас '
    'не получается подключиться, попробуйте выбрать другой сервер или путь в приложении, '
    'либо напишите в поддержку.'
)

NO_ROUTER_DATA: Final[str] = (
    'Данных именно с вашего устройства (роутера) у нас пока нет — эта часть проверки не подключена.'
)


def _subscription_lines(user_vpn, balance) -> list[str]:
    if not user_vpn.enabled:
        return [SUBSCRIPTION_DEAD]

    price = daily_price(user_vpn)
    days = max(int(balance // price), 0) if price > 0 else 0
    if days <= 0:
        return [SUBSCRIPTION_DEAD]

    return [f'Подписка активна, баланса хватит примерно на {days} дней.']


def _humanize_age(delta: timezone.timedelta) -> str:
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return 'меньше часа назад'
    if hours < 24:
        return f'{int(hours)} ч. назад'
    return f'{int(hours // 24)} дн. назад'


def _device_lines(devices) -> list[str]:
    if not devices:
        return [NO_DEVICES]

    now = timezone.now()
    lines = []
    for index, device in enumerate(devices, start=1):
        age = now - device.last_seen_at
        line = f'{index}. {html.escape(device_display_name(device))} — {_humanize_age(age)}.'
        if age >= DEVICE_STALE_AFTER:
            line += DEVICE_STALE_HINT
        lines.append(line)
    return lines


def _monitor_lines(state: MonitorState | None) -> list[str]:
    if state is None:
        return [NO_MONITORING]
    if timezone.now() - state.checked_at > MONITOR_STALE_AFTER:
        return [MONITOR_UNKNOWN]

    endpoints = state.details.get('endpoints') if isinstance(state.details, dict) else None
    if not endpoints:
        return [NO_MONITORING]

    lines = []
    any_down = False
    for index, endpoint in enumerate(endpoints, start=1):
        ok = bool(endpoint.get('ok')) if isinstance(endpoint, dict) else False
        any_down = any_down or not ok
        lines.append(f'Точка {index}: {"работает" if ok else "недоступна"}')
    if any_down:
        lines.append(ENDPOINT_DOWN_HINT)
    return lines


async def build_selfcheck_screen(user: TelegramUser) -> tuple[str, InlineKeyboardMarkup]:
    user_vpn = await _subscription(user)
    if user_vpn is None:
        return screen('Самопроверка', body=[NO_SUBSCRIPTION]), await get_reply_markup_selfcheck()

    user_with_balance = await TelegramUser.objects.annotate_balance().aget(id=user.id)
    balance = user_with_balance.balance

    devices = await sync_to_async(bound_devices)(user_vpn)
    limit = device_limit_for(user_vpn)
    monitor_state = await MonitorState.objects.filter(layer='l1').afirst()

    body = [
        '\n'.join([bold('Подписка'), *_subscription_lines(user_vpn, balance)]),
        '\n'.join([bold('Устройства'), f'Мест: {limit}, занято {len(devices)}.']),
        '\n'.join([bold('Когда устройства были на связи'), *_device_lines(devices)]),
        '\n'.join([bold('Наши точки подключения'), *_monitor_lines(monitor_state), NO_ROUTER_DATA]),
    ]

    return screen('Самопроверка', body=body), await get_reply_markup_selfcheck()


async def show_selfcheck(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user: TelegramUser = await get_user(update)
    text, keyboard = await build_selfcheck_screen(user)
    await render_screen(update, context, text, keyboard)
