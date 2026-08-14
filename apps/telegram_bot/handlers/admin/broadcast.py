"""Broadcasts from the in-bot admin panel, sharing `broadcast_ops` with Django admin.

This is money-adjacent: it reaches real customers. The confirm screen shows
audience, exact recipient count and the message text back to the admin before
the send button does anything.
"""
from __future__ import annotations

import html
import logging

from asgiref.sync import sync_to_async
from django.contrib.auth.models import User
from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from apps.telegram_bot import broadcast_ops
from apps.telegram_bot.admin_auth import admin_only
from apps.telegram_bot.handlers.admin.common import (
    AWAITING_BROADCAST_MESSAGE,
    AWAITING_BROADCAST_TITLE,
    begin_awaiting,
    stop_awaiting,
)
from apps.telegram_bot.models import Broadcast
from apps.telegram_bot.ui import answer_query, back_button, bold, button, render_screen, screen


logger = logging.getLogger(__name__)

# A broadcast created from the bot has no Django session behind it — only a
# Telegram id, which `auth.User` cannot hold. A dedicated, unusable service
# account keeps `Broadcast.created_by` (NOT NULL) satisfied without a schema
# change, and lets Django admin's own list distinguish a bot-sent broadcast
# from an operator-sent one at a glance.
BOT_SERVICE_ACCOUNT_USERNAME = 'telegram_bot_admin'

AUDIENCE_LABELS = dict(Broadcast.AUDIENCE_CHOICES)


async def _bot_service_account() -> User:
    account, _ = await User.objects.aget_or_create(
        username=BOT_SERVICE_ACCOUNT_USERNAME,
        defaults={'is_active': False, 'is_staff': False},
    )
    return account


@admin_only
async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    stop_awaiting(
        context, 'admin_broadcast_audience', 'admin_broadcast_title', 'admin_broadcast_id', 'admin_broadcast_digest',
    )
    buttons = [[button(label, f'admin_broadcast_audience:{value}')] for value, label in Broadcast.AUDIENCE_CHOICES]
    buttons.append([back_button('admin_menu')])
    text = screen('Рассылка', body=['Выберите аудиторию.'])
    await render_screen(update, context, text, InlineKeyboardMarkup(inline_keyboard=buttons))


@admin_only
async def admin_broadcast_audience(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    audience = update.callback_query.data.split(':', 1)[1]
    if audience not in AUDIENCE_LABELS:
        await answer_query(update, 'Неизвестная аудитория.')
        return
    begin_awaiting(context, AWAITING_BROADCAST_TITLE, admin_broadcast_audience=audience)
    text = screen('Рассылка', body=['Введите заголовок рассылки (виден только в админке, не получателям).'])
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[back_button('admin_broadcast')]])
    await render_screen(update, context, text, keyboard)


async def handle_title_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    audience = context.user_data.get('admin_broadcast_audience')
    if not audience:
        stop_awaiting(context)
        return

    title = (update.message.text or '').strip()
    if not title or len(title) > 200:
        text = screen('Рассылка', body=['Заголовок должен быть от 1 до 200 символов. Введите снова.'])
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[back_button('admin_broadcast')]])
        await render_screen(update, context, text, keyboard)
        return

    begin_awaiting(context, AWAITING_BROADCAST_MESSAGE, admin_broadcast_audience=audience, admin_broadcast_title=title)
    text = screen(
        'Рассылка',
        body=[f'Введите текст сообщения ({broadcast_ops.MESSAGE_MIN_LENGTH}–{broadcast_ops.MESSAGE_MAX_LENGTH} символов).'],
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[back_button('admin_broadcast')]])
    await render_screen(update, context, text, keyboard)


async def handle_message_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    audience = context.user_data.get('admin_broadcast_audience')
    title = context.user_data.get('admin_broadcast_title')
    if not audience or not title:
        stop_awaiting(context)
        return

    message = (update.message.text or '').strip()
    if not (broadcast_ops.MESSAGE_MIN_LENGTH <= len(message) <= broadcast_ops.MESSAGE_MAX_LENGTH):
        text = screen(
            'Рассылка',
            body=[
                f'Сообщение должно быть от {broadcast_ops.MESSAGE_MIN_LENGTH} до '
                f'{broadcast_ops.MESSAGE_MAX_LENGTH} символов. Введите снова.',
            ],
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[back_button('admin_broadcast')]])
        await render_screen(update, context, text, keyboard)
        return

    account = await _bot_service_account()
    broadcast = await Broadcast.objects.acreate(title=title, message=message, audience=audience, created_by=account)
    broadcast = await sync_to_async(broadcast_ops.create_preview_snapshot)(broadcast.id)
    if broadcast is None:
        stop_awaiting(context, 'admin_broadcast_audience', 'admin_broadcast_title')
        text = screen('Рассылка', body=['Не удалось подготовить снимок получателей. Начните заново.'])
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[back_button('admin_menu')]])
        await render_screen(update, context, text, keyboard)
        return

    stop_awaiting(context, 'admin_broadcast_audience', 'admin_broadcast_title')
    context.user_data['admin_broadcast_id'] = broadcast.id
    context.user_data['admin_broadcast_digest'] = broadcast_ops.confirmation_digest(broadcast)
    await _confirm_screen(update, context, broadcast)


async def _confirm_screen(update: Update, context: ContextTypes.DEFAULT_TYPE, broadcast: Broadcast) -> None:
    text = screen(
        'Подтвердите рассылку',
        state=[f'Аудитория: {broadcast.get_audience_display()}', f'Получателей: {broadcast.total_users}'],
        body=[bold(broadcast.title), html.escape(broadcast.message)],
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [button('Отправить', 'admin_broadcast_send'), button('Отменить', 'admin_broadcast_cancel')],
    ])
    await render_screen(update, context, text, keyboard)


@admin_only
async def admin_broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    broadcast_id = context.user_data.pop('admin_broadcast_id', None)
    digest = context.user_data.pop('admin_broadcast_digest', None)
    if broadcast_id is None or digest is None:
        await answer_query(update, 'Начните заново.')
        return

    result = await sync_to_async(broadcast_ops.queue_confirmed_broadcast)(broadcast_id, digest)
    if not result.ok:
        text = screen('Рассылка', body=['Рассылка или снимок получателей изменились. Начните заново.'])
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[back_button('admin_menu')]])
        await render_screen(update, context, text, keyboard)
        return

    logger.info('admin_broadcast_send telegram_admin_id=%s broadcast_id=%s', update.effective_user.id, broadcast_id)
    text = screen('Рассылка', body=['Рассылка поставлена в очередь.'])
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[back_button('admin_menu')]])
    await render_screen(update, context, text, keyboard, toast='Поставлено в очередь.')


@admin_only
async def admin_broadcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    broadcast_id = context.user_data.pop('admin_broadcast_id', None)
    context.user_data.pop('admin_broadcast_digest', None)
    if broadcast_id is not None:
        await sync_to_async(broadcast_ops.cancel_confirming_broadcast)(broadcast_id)
    text = screen('Рассылка', body=['Отменено.'])
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[back_button('admin_menu')]])
    await render_screen(update, context, text, keyboard, toast='Отменено.')
