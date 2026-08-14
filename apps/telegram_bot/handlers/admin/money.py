"""Money and provisioning actions reached from a client's card.

Every execute handler re-verifies state immediately before acting rather than
trusting what the confirm screen showed — the same discipline
`apps.telegram_bot.handlers.devices.unbind_one_device` already uses for a
button that might answer a screen nobody is looking at anymore.
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.payments.models import Transaction
from apps.servers.models import Server
from apps.telegram_bot.admin_auth import admin_only
from apps.telegram_bot.handlers.admin.client import client_label, render_client_card
from apps.telegram_bot.handlers.admin.common import AWAITING_BALANCE_AMOUNT, begin_awaiting, stop_awaiting
from apps.telegram_bot.ui import answer_query, back_button, bold, button, render_screen, screen
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN
from apps.vpn.services.add_vpn_to_user import add_vpn_to_user
from apps.vpn.services.remove_vpn_user_from_server import disable_vpn_user_from_server


logger = logging.getLogger(__name__)

# A manual credit has no undo button. This is a fat-finger ceiling, not a
# business rule — raise it in code if a legitimate credit needs to be larger.
MAX_CREDIT_AMOUNT = Decimal('100000')


def _parse_amount(raw: str) -> Decimal | None:
    try:
        amount = Decimal(raw.strip().replace(',', '.'))
    except (InvalidOperation, AttributeError):
        return None
    if amount <= 0 or amount > MAX_CREDIT_AMOUNT:
        return None
    return amount.quantize(Decimal('0.01'))


# --- Balance credit -----------------------------------------------------


@admin_only
async def admin_credit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = int(update.callback_query.data.split(':')[1])
    begin_awaiting(context, AWAITING_BALANCE_AMOUNT, admin_credit_user_id=user_id)
    text = screen('Начислить баланс', body=[f'Введите сумму в рублях (0–{MAX_CREDIT_AMOUNT}).'])
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[back_button(f'admin_client_view:{user_id}')]])
    await render_screen(update, context, text, keyboard)


async def handle_amount_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.user_data.get('admin_credit_user_id')
    if user_id is None:
        stop_awaiting(context)
        return

    amount = _parse_amount(update.message.text or '')
    if amount is None:
        text = screen('Начислить баланс', body=[f'Не понял сумму. Введите число от 0 до {MAX_CREDIT_AMOUNT}.'])
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[back_button(f'admin_client_view:{user_id}')]])
        await render_screen(update, context, text, keyboard)
        return

    user = await TelegramUser.objects.filter(id=user_id).afirst()
    if user is None:
        stop_awaiting(context, 'admin_credit_user_id')
        await render_screen(update, context, screen('Начислить баланс', body=['Клиент больше не существует.']),
                             InlineKeyboardMarkup(inline_keyboard=[[back_button('admin_menu')]]))
        return

    stop_awaiting(context, 'admin_credit_user_id')
    context.user_data['admin_credit_pending_user_id'] = user_id
    context.user_data['admin_credit_pending_amount'] = str(amount)
    text = screen(
        'Подтвердите начисление',
        body=[f'Клиент: {bold(client_label(user))}', f'Сумма: {bold(f"{amount} руб.")}'],
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [button('Подтвердить', 'admin_credit_execute'), button('Отмена', f'admin_client_view:{user_id}')],
    ])
    await render_screen(update, context, text, keyboard)


@admin_only
async def admin_credit_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.user_data.pop('admin_credit_pending_user_id', None)
    amount_raw = context.user_data.pop('admin_credit_pending_amount', None)
    if user_id is None or amount_raw is None:
        await answer_query(update, 'Начните заново.')
        return

    user = await TelegramUser.objects.filter(id=user_id).afirst()
    if user is None:
        await answer_query(update, 'Клиент больше не существует.')
        return

    amount = Decimal(amount_raw)
    await Transaction.objects.acreate(
        user=user, amount=amount, status=TransactionStatusChoices.SUCCESS, source=TransactionSourceChoices.MANUAL,
    )
    logger.info(
        'admin_credit telegram_admin_id=%s target_user_id=%s amount=%s',
        update.effective_user.id, user_id, amount,
    )
    await render_client_card(update, context, user_id, toast=f'Начислено {amount} руб.')


# --- VPN issue ------------------------------------------------------------


async def _vpn_issue_confirm_screen(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, server: Server) -> None:
    label = server.name or f'сервер {server.id}'
    text = screen(
        'Подтвердите выдачу VPN',
        body=[f'Сервер: {bold(label)}', f'Цена: {bold(f"{server.tariff.price} руб./сутки")}'],
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            button('Подтвердить', f'admin_vpn_issue_execute:{user_id}:{server.id}'),
            button('Отмена', f'admin_client_view:{user_id}'),
        ],
    ])
    await render_screen(update, context, text, keyboard)


@admin_only
async def admin_vpn_issue_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = int(update.callback_query.data.split(':')[1])
    servers = [server async for server in Server.objects.with_related_tariffs().order_by('id')]
    if not servers:
        await answer_query(update, 'Нет доступных серверов.')
        return
    if len(servers) == 1:
        await _vpn_issue_confirm_screen(update, context, user_id, servers[0])
        return
    buttons = [
        [button(server.name or f'сервер {server.id}', f'admin_vpn_issue_confirm:{user_id}:{server.id}')]
        for server in servers
    ]
    buttons.append([back_button(f'admin_client_view:{user_id}')])
    text = screen('Выдать VPN', body=['Выберите сервер.'])
    await render_screen(update, context, text, InlineKeyboardMarkup(inline_keyboard=buttons))


@admin_only
async def admin_vpn_issue_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _, user_id_raw, server_id_raw = update.callback_query.data.split(':')
    server = await Server.objects.with_related_tariffs().filter(id=int(server_id_raw)).afirst()
    if server is None:
        await answer_query(update, 'Сервер больше не существует.')
        return
    await _vpn_issue_confirm_screen(update, context, int(user_id_raw), server)


@admin_only
async def admin_vpn_issue_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _, user_id_raw, server_id_raw = update.callback_query.data.split(':')
    user_id, server_id = int(user_id_raw), int(server_id_raw)
    user = await TelegramUser.objects.filter(id=user_id).afirst()
    server = await Server.objects.with_related_tariffs().filter(id=server_id).afirst()
    if user is None or server is None:
        await answer_query(update, 'Клиент или сервер больше не существует.')
        return
    await add_vpn_to_user(user, server)
    logger.info(
        'admin_vpn_issue telegram_admin_id=%s target_user_id=%s server_id=%s',
        update.effective_user.id, user_id, server_id,
    )
    await render_client_card(update, context, user_id, toast='Подписка выдана.')


# --- VPN disable ------------------------------------------------------------


@admin_only
async def admin_vpn_disable_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_vpn_id = int(update.callback_query.data.split(':')[1])
    user_vpn = await UserVPN.objects.select_related('server', 'user').filter(id=user_vpn_id).afirst()
    if user_vpn is None or not user_vpn.enabled:
        await answer_query(update, 'Подписка уже отключена или не существует.')
        return
    label = user_vpn.server.name or f'сервер {user_vpn.server_id}'
    text = screen(
        'Подтвердите отключение',
        body=[f'Клиент: {bold(client_label(user_vpn.user))}', f'Сервер: {bold(label)}'],
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            button('Подтвердить', f'admin_vpn_disable_execute:{user_vpn_id}'),
            button('Отмена', f'admin_client_view:{user_vpn.user_id}'),
        ],
    ])
    await render_screen(update, context, text, keyboard)


@admin_only
async def admin_vpn_disable_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_vpn_id = int(update.callback_query.data.split(':')[1])
    user_vpn = await UserVPN.objects.select_related('server', 'user').filter(id=user_vpn_id).afirst()
    if user_vpn is None or not user_vpn.enabled:
        await answer_query(update, 'Подписка уже отключена или не существует.')
        return
    await disable_vpn_user_from_server(user_vpn)
    logger.info(
        'admin_vpn_disable telegram_admin_id=%s user_vpn_id=%s target_user_id=%s',
        update.effective_user.id, user_vpn_id, user_vpn.user_id,
    )
    await render_client_card(update, context, user_vpn.user_id, toast='Подписка отключена.')
