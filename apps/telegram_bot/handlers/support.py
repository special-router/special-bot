"""Обращения в поддержку внутри бота: клиент здесь, операторы — в темах чата.

Разговор клиента и оператора идёт через две разные точки входа: сообщение из
личного чата уходит в тему, сообщение из темы возвращается клиенту. Общего у
них только тикет, и весь порядок работы с ним — в `apps.telegram_bot.support`.
"""

from __future__ import annotations

import html
from typing import Final

from asgiref.sync import sync_to_async
from django.conf import settings
from telegram import InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from apps.telegram_bot import icons
from apps.telegram_bot.inline_buttons.back import get_reply_markup_back
from apps.telegram_bot.models import SupportTicket
from apps.telegram_bot.support import (
    attach_topic,
    close_ticket,
    consume_prompt,
    discard_draft,
    open_prompt,
    open_ticket,
    remember_topic_message,
    ticket_by_topic,
    truncate,
)
from apps.telegram_bot.ui import (
    NO_PREVIEW,
    PARSE_MODE,
    STATUS_ACTIVE,
    STATUS_INACTIVE,
    back_button,
    button,
    render_screen,
    screen,
)
from apps.telegram_bot.utils import get_user
from apps.users.models import TelegramUser


INVITATION_BODY: Final[str] = (
    'Опишите проблему одним сообщением — оно уйдёт оператору, ответ придёт сюда же. '
    'Чтобы дополнить обращение, нажмите «Поддержка» ещё раз.'
)

UNAVAILABLE_BODY: Final[str] = (
    'Не удалось создать обращение. Попробуйте ещё раз через несколько минут — '
    'если не получится, напишите нам в чат поддержки.'
)

# Bot API обрезает имя темы на 128 символах; обрезаем сами, чтобы не гадать,
# что именно уцелело из имени пользователя.
TOPIC_NAME_MAX_LENGTH: Final[int] = 128


def _enabled() -> bool:
    """Вторая застава помимо регистрации обработчиков.

    Регистрация читает настройку один раз при старте; обработчик — на каждом
    вызове. Обе проверки дублируют друг друга намеренно: включение функции
    зависит от чата, которого может не быть, и цена ошибки здесь — тикет,
    который некуда положить.
    """
    return bool(settings.SUPPORT_CHAT_ID)


def _mention(telegram_user) -> str:
    """Как обращение подписано для оператора.

    У пользователя может не быть `@username` вовсе, а числовой id ищется в
    админке — поэтому запасной вариант тоже пригоден для работы, а не заглушка.
    """
    if telegram_user is not None and telegram_user.username:
        return f'@{telegram_user.username}'
    return f'id{telegram_user.id}' if telegram_user is not None else 'id0'


def _topic_name(ticket_id: int, mention: str, *, closed: bool = False) -> str:
    marker = STATUS_INACTIVE if closed else STATUS_ACTIVE
    return truncate(f'{marker} Ticket #{ticket_id} | {mention}', TOPIC_NAME_MAX_LENGTH)


async def build_support_screen() -> tuple[str, InlineKeyboardMarkup]:
    """Приглашение написать. Экран собирается без базы — как остальные."""
    text = screen(
        'Поддержка',
        state=['Следующее ваше сообщение уйдёт оператору.'],
        body=[INVITATION_BODY],
    )
    return text, await get_reply_markup_back()


async def build_sent_screen(ticket_id: int) -> tuple[str, InlineKeyboardMarkup]:
    text = screen(
        'Обращение отправлено',
        state=[f'Обращение № {ticket_id}'],
        body=['Оператор ответит в этом чате. Чтобы что-то добавить, нажмите «Поддержка» ещё раз.'],
    )
    return text, await get_reply_markup_back()


async def build_unavailable_screen() -> tuple[str, InlineKeyboardMarkup]:
    return screen('Поддержка', body=[UNAVAILABLE_BODY]), await get_reply_markup_back()


def _reply_keyboard() -> InlineKeyboardMarkup:
    """Ответ оператора — тупик без кнопок, поэтому продолжение предлагается сразу."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[button('Поддержка', 'support_open', icon=icons.PEOPLE), back_button()]]
    )


def _close_keyboard(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[button('Закрыть обращение', f'support_close:{ticket_id}')]])


async def support_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопка «Поддержка»: включить ожидание и позвать написать."""
    if not _enabled():
        return

    user: TelegramUser = await get_user(update)
    await sync_to_async(open_prompt)(user.id)

    text, keyboard = await build_support_screen()
    await render_screen(update, context, text, keyboard)


async def support_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Текст из личного чата: уходит в тикет, если его ждали.

    Ожидание снимается первым делом. Если снять его позже, любой сбой на
    стороне Telegram оставил бы пользователя в режиме, где каждое следующее
    слово улетает оператору.
    """
    if not _enabled():
        return

    user: TelegramUser = await get_user(update)
    if not await sync_to_async(consume_prompt)(user.id):
        return

    message_text = truncate(update.message.text)
    if not message_text:
        return

    mention = _mention(update.effective_user)
    ticket, created = await sync_to_async(open_ticket)(user.id, mention, message_text)

    if created:
        if not await _start_topic(context, ticket, mention):
            text, keyboard = await build_unavailable_screen()
            await render_screen(update, context, text, keyboard)
            return

    await _post_to_topic(context, ticket, mention, message_text)

    text, keyboard = await build_sent_screen(ticket.id)
    await render_screen(update, context, text, keyboard)


async def _start_topic(context: ContextTypes.DEFAULT_TYPE, ticket: SupportTicket, mention: str) -> bool:
    """Завести тему и привязать её к тикету. ``False`` — Telegram отказал.

    Тема привязывается сразу после создания, до первого сообщения в ней: тикет
    без темы нельзя ни закрыть, ни продолжить, и он навсегда занимает
    единственный открытый слот пользователя. Поэтому отказ в создании темы
    обязан убрать черновик, а всё, что после — уже поправимо.
    """
    try:
        topic = await context.bot.create_forum_topic(
            chat_id=settings.SUPPORT_CHAT_ID, name=_topic_name(ticket.id, mention)
        )
    except TelegramError:
        await sync_to_async(discard_draft)(ticket.id)
        return False

    ticket.topic_id = topic.message_thread_id
    await sync_to_async(attach_topic)(ticket.id, topic.message_thread_id)
    return True


async def _post_to_topic(
    context: ContextTypes.DEFAULT_TYPE, ticket: SupportTicket, mention: str, message_text: str
) -> None:
    text = screen(
        f'Обращение № {ticket.id}',
        state=[f'От: {html.escape(mention)}'],
        body=[html.escape(message_text)],
    )
    posted = await context.bot.send_message(
        chat_id=settings.SUPPORT_CHAT_ID,
        message_thread_id=ticket.topic_id,
        text=text,
        reply_markup=_close_keyboard(ticket.id),
        parse_mode=PARSE_MODE,
        link_preview_options=NO_PREVIEW,
    )
    await sync_to_async(remember_topic_message)(ticket.id, posted.message_id)


async def support_operator_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Сообщение из темы чата операторов — обратно клиенту."""
    message = update.message
    if message.from_user is not None and message.from_user.is_bot:
        # Собственные публикации бота в теме — не ответ оператора.
        return

    if message.message_thread_id is None:
        return

    ticket = await sync_to_async(ticket_by_topic)(message.message_thread_id)
    if ticket is None:
        # Тема закрытого обращения остаётся в чате и остаётся доступной для
        # письма. Для клиента разговор закончен, и продолжать его тут нечем.
        return

    text = screen('Ответ поддержки', state=[f'Обращение № {ticket.id}'], body=[html.escape(truncate(message.text))])
    await context.bot.send_message(
        chat_id=ticket.user.telegram_id,
        text=text,
        reply_markup=_reply_keyboard(),
        parse_mode=PARSE_MODE,
        link_preview_options=NO_PREVIEW,
    )


async def support_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопка оператора «Закрыть обращение»."""
    query = update.callback_query
    ticket_id = int(query.data.split(':')[1])

    ticket = await sync_to_async(close_ticket)(ticket_id, _mention(query.from_user))
    if ticket is None:
        await query.answer('Обращение уже закрыто.')
        return

    await query.answer('Обращение закрыто.')

    if ticket.topic_id is not None:
        await context.bot.edit_forum_topic(
            chat_id=settings.SUPPORT_CHAT_ID,
            message_thread_id=ticket.topic_id,
            name=_topic_name(ticket.id, ticket.telegram_username, closed=True),
        )
        await context.bot.send_message(
            chat_id=settings.SUPPORT_CHAT_ID,
            message_thread_id=ticket.topic_id,
            text=screen(
                f'Обращение № {ticket.id} закрыто',
                state=[f'Закрыл: {html.escape(_mention(query.from_user))}'],
            ),
            parse_mode=PARSE_MODE,
            link_preview_options=NO_PREVIEW,
        )

    text = screen(
        'Обращение закрыто',
        state=[f'Обращение № {ticket.id}'],
        body=['Если вопрос остался, откройте новое обращение кнопкой «Поддержка».'],
    )
    await context.bot.send_message(
        chat_id=ticket.user.telegram_id,
        text=text,
        reply_markup=_reply_keyboard(),
        parse_mode=PARSE_MODE,
        link_preview_options=NO_PREVIEW,
    )
