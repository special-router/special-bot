"""Read-only client lookup and card for the in-bot admin panel.

Purely read-only: this screen never mutates anything. Money and provisioning
actions live in `apps.telegram_bot.handlers.admin.money` and are reached from
the buttons this card renders.
"""
from __future__ import annotations

import html

from asgiref.sync import sync_to_async
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from apps.analytics.balance_split import split_balance
from apps.subscriptions.devices import bound_devices, device_limit_for
from apps.subscriptions.pricing import daily_price, paid_device_slots
from apps.telegram_bot.admin_auth import admin_only
from apps.telegram_bot.handlers.admin.common import AWAITING_CLIENT_LOOKUP, begin_awaiting, stop_awaiting
from apps.telegram_bot.handlers.devices import device_display_name
from apps.telegram_bot.ui import answer_query, back_button, bold, button, code, render_screen, screen
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


LOOKUP_PROMPT = 'Введите @username или Telegram ID клиента.'
NOT_FOUND = 'Клиент не найден.'


def client_label(user: TelegramUser) -> str:
    return f'@{user.username}' if user.username else str(user.telegram_id)


async def _find_client(identifier: str) -> TelegramUser | None:
    identifier = identifier.strip()
    if not identifier:
        return None
    if identifier.startswith('@'):
        identifier = identifier[1:]
    if identifier.isdigit():
        user = await TelegramUser.objects.annotate_balance().filter(telegram_id=int(identifier)).afirst()
        if user is not None:
            return user
        return None
    return await TelegramUser.objects.annotate_balance().filter(username__iexact=identifier).afirst()


def _subscription_lines(user_vpn: UserVPN, devices: list) -> list[str]:
    status = 'включена' if user_vpn.enabled else 'отключена'
    price = daily_price(user_vpn)
    limit = device_limit_for(user_vpn)
    paid = paid_device_slots(user_vpn)
    lines = [
        f'{bold(user_vpn.server.name or f"сервер {user_vpn.server_id}")} — {status}, {price} руб./сутки, '
        f'мест {limit} (платных {paid}), занято {len(devices)}',
    ]
    lines.extend(
        f'&nbsp;&nbsp;{index}. {html.escape(device_display_name(device))}'
        for index, device in enumerate(devices, start=1)
    )
    return lines


async def build_client_card(user: TelegramUser) -> tuple[str, InlineKeyboardMarkup]:
    split = await sync_to_async(split_balance)(user.id)
    subscriptions = [
        vpn
        async for vpn in UserVPN.objects.select_related('server__tariff').filter(user=user).order_by('created_at', 'id')
    ]

    state = [
        f'Telegram ID: {code(user.telegram_id)}',
        f'Баланс: {user.balance} руб. (реальных {split.real}, бонусных {split.bonus})',
    ]

    body: list[str] = []
    buttons: list[list[InlineKeyboardButton]] = []
    if not subscriptions:
        body.append('Подписок нет.')
    for user_vpn in subscriptions:
        devices = await sync_to_async(bound_devices)(user_vpn)
        body.append('\n'.join(_subscription_lines(user_vpn, devices)))
        if user_vpn.enabled:
            label = user_vpn.server.name or f'сервер {user_vpn.server_id}'
            buttons.append([button(f'Отключить: {label}', f'admin_vpn_disable:{user_vpn.id}')])

    buttons.append([
        button('Начислить баланс', f'admin_credit:{user.id}'),
        button('Выдать VPN', f'admin_vpn_issue:{user.id}'),
    ])
    buttons.append([back_button('admin_menu')])

    title = f'Клиент {client_label(user)}'
    text = screen(title, state=state, body=body)
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


async def render_client_card(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, *, toast: str | None = None
) -> None:
    user = await TelegramUser.objects.annotate_balance().filter(id=user_id).afirst()
    if user is None:
        await answer_query(update, 'Клиент больше не существует.')
        return
    text, keyboard = await build_client_card(user)
    await render_screen(update, context, text, keyboard, toast=toast)


@admin_only
async def admin_client(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    begin_awaiting(context, AWAITING_CLIENT_LOOKUP)
    text = screen('Поиск клиента', body=[LOOKUP_PROMPT])
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[back_button('admin_menu')]])
    await render_screen(update, context, text, keyboard)


@admin_only
async def admin_client_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = int(update.callback_query.data.split(':')[1])
    await render_client_card(update, context, user_id)


async def handle_lookup_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    identifier = (update.message.text or '').strip()
    user = await _find_client(identifier)
    if user is None:
        text = screen('Поиск клиента', body=[NOT_FOUND, LOOKUP_PROMPT])
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[back_button('admin_menu')]])
        await render_screen(update, context, text, keyboard)
        return
    stop_awaiting(context)
    text, keyboard = await build_client_card(user)
    await render_screen(update, context, text, keyboard)
