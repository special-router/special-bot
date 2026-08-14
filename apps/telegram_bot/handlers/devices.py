"""Устройства подписки: сколько мест куплено и кто их занял.

Число подписок из бота больше не регулируется — у аккаунта она одна. Регулируется
то, ради чего подписки и заводили по нескольку: сколько устройств она обслуживает.
Место покупается и продаётся отдельно, устройство отвязывается поимённо.

Разница между местом и устройством здесь принципиальна и видна на экране.
Место — это то, за что идут деньги, и оно остаётся купленным, когда устройство
ушло. Устройство — то, что место занимает. Раньше на оба понятия приходилась одна
кнопка «Отвязать устройства», которая молча стирала все привязки разом.
"""
from __future__ import annotations

import html
from typing import Final

from asgiref.sync import sync_to_async
from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.payments.models import Transaction
from apps.subscriptions.devices import bound_devices, device_limit_for, set_device_limit, unbind_device
from apps.subscriptions.pricing import free_device_slots, slot_price
from apps.telegram_bot.handlers.balance import build_balance_screen
from apps.telegram_bot.inline_buttons.devices import get_reply_markup_devices
from apps.telegram_bot.ui import answer_query, bold, render_screen, screen
from apps.telegram_bot.utils import get_user
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


UNNAMED_DEVICE: Final[str] = 'без названия'

NO_SUBSCRIPTION: Final[str] = 'Подписки нет — сначала подключите её на экране «Подписки».'

EMPTY_HINT: Final[str] = (
    'Свободное место занимает то устройство, которое откроет подписку следующим: '
    'откройте приложение там, где хотите ей пользоваться.'
)


async def _subscription(user: TelegramUser) -> UserVPN | None:
    """Единственная подписка аккаунта — самая старая, если их осталось несколько.

    Тариф подтягивается вместе с сервером: цена места спрашивается на каждом
    экране, а `server.tariff` из асинхронного кода уходит в синхронный запрос и
    роняет обработчик, а не замедляет его.
    """
    return await (
        UserVPN.objects.select_related('server__tariff')
        .filter(user=user).order_by('created_at', 'id').afirst()
    )


async def build_devices_screen(user: TelegramUser, *, notice: str | None = None) -> tuple[str, InlineKeyboardMarkup]:
    user_vpn = await _subscription(user)
    if user_vpn is None:
        return screen('Устройства', body=[notice, NO_SUBSCRIPTION]), await get_reply_markup_devices([])

    devices = await sync_to_async(bound_devices)(user_vpn)
    limit = device_limit_for(user_vpn)
    free = free_device_slots()
    paid = max(0, limit - free)

    lines = [
        f'{bold(f"{index}.")} {html.escape(_device_name(device))}'
        for index, device in enumerate(devices, start=1)
    ]
    price = slot_price(user_vpn)
    state = [
        f'Мест: {limit}, занято {len(devices)}',
        f'Из них платных: {paid} по {price} руб. в сутки' if paid else f'Все {free} входят в подписку',
    ]

    return (
        screen('Устройства', state=state, body=[notice, *lines] if lines else [notice, EMPTY_HINT]),
        await get_reply_markup_devices(devices, can_drop=limit > free),
    )


def _device_name(device) -> str:
    return device.device_model or device.device_os or UNNAMED_DEVICE


async def show_devices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user: TelegramUser = await get_user(update)
    text, keyboard = await build_devices_screen(user)
    await render_screen(update, context, text, keyboard)


async def add_device_slot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Купить одно место. Сутки за него списываются сразу, как за подписку.

    Дальше место оплачивается вместе с подпиской ежедневным списанием, поэтому
    здесь берётся ровно один день — тот, что уже идёт.
    """
    user: TelegramUser = await get_user(update)
    user_vpn = await _subscription(user)
    if user_vpn is None:
        await answer_query(update, 'Сначала подключите подписку.')
        return

    price = slot_price(user_vpn)
    if user.balance < price:
        text, keyboard = await build_balance_screen(user, notice='Недостаточно средств для нового места.')
        await render_screen(update, context, text, keyboard)
        return

    await sync_to_async(set_device_limit)(user_vpn, device_limit_for(user_vpn) + 1)
    await Transaction.objects.acreate(
        user=user,
        amount=-price,
        status=TransactionStatusChoices.SUCCESS,
        source=TransactionSourceChoices.BUY,
    )

    # Баланс аннотирован до списания — пользователь перечитывается, иначе экран
    # покажет сумму, которой уже нет.
    text, keyboard = await build_devices_screen(await get_user(update), notice='Место добавлено.')
    await render_screen(update, context, text, keyboard, toast='Место добавлено.')


async def drop_device_slot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Перестать платить за одно место.

    Занятое место не убирается: иначе клиент платил бы меньше, а пользовался
    по-прежнему, — и узнал бы об этом, только когда устройство перестало
    работать. Сначала отвязать, потом убрать.
    """
    user: TelegramUser = await get_user(update)
    user_vpn = await _subscription(user)
    if user_vpn is None:
        await answer_query(update, 'Сначала подключите подписку.')
        return

    limit = device_limit_for(user_vpn)
    if limit <= free_device_slots():
        await answer_query(update, 'Это места, входящие в подписку, — убрать их нельзя.')
        return

    devices = await sync_to_async(bound_devices)(user_vpn)
    if len(devices) >= limit:
        await answer_query(update, 'Все места заняты. Сначала отвяжите устройство.')
        return

    await sync_to_async(set_device_limit)(user_vpn, limit - 1)
    text, keyboard = await build_devices_screen(user, notice='Место убрано, платы за него больше нет.')
    await render_screen(update, context, text, keyboard, toast='Место убрано.')


async def unbind_one_device(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Освободить место, занятое одним конкретным устройством."""
    user: TelegramUser = await get_user(update)
    device_id = int(update.callback_query.data.split(':')[1])

    if not await sync_to_async(unbind_device)(user.id, device_id):
        # Экран мог устареть: устройство уже отвязано другим нажатием.
        await answer_query(update, 'Устройство не найдено.')
        return

    text, keyboard = await build_devices_screen(user, notice='Устройство отвязано, место свободно.')
    await render_screen(update, context, text, keyboard, toast='Устройство отвязано.')
