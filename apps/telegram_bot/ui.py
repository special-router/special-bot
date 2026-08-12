"""Сборка экранов бота: разметка текста, кнопки и якорное сообщение.

Одна беседа — одно сообщение. Раньше каждое действие отправляло новое
сообщение, и чат зарастал мёртвыми экранами; здесь экран перерисовывается
поверх того сообщения, на кнопку которого нажали. Новое сообщение уходит
только там, где редактировать нечего: `/start`, ответ на команду и экран,
которому предшествует альбом с картинками.
"""

from __future__ import annotations

import contextlib
import html
import inspect
from typing import Iterable

from django.conf import settings
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from apps.telegram_bot import icons
from apps.telegram_bot.icons import Icon


PARSE_MODE = 'HTML'
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

# Маркеры статуса в списке — единственное место, где эмодзи допустимы в тексте.
STATUS_ACTIVE = '✅'
STATUS_INACTIVE = '❌'

# Поля Bot API 10.x PTB оборачивает начиная с 22.8. На более раннем пине то же
# поле уходит через `api_kwargs` — штатный для PTB путь для ещё не обёрнутых
# полей, — поэтому иконки не зависят от версии библиотеки.
_NATIVE_ICON_FIELD = 'icon_custom_emoji_id' in inspect.signature(InlineKeyboardButton.__init__).parameters

# Ошибки Bot API, при которых редактирование бессмысленно, но пользователю
# показывать нечего: сообщение уже такое же либо слишком старое для правки.
_UNCHANGED = 'message is not modified'
_UNEDITABLE = (
    'message to edit not found',
    "message can't be edited",
    'message_id_invalid',
    'there is no text in the message to edit',
)


def icons_enabled() -> bool:
    return bool(getattr(settings, 'TELEGRAM_BUTTON_ICONS_ENABLED', False))


def button(
    text: str,
    callback_data: str | None = None,
    *,
    url: str | None = None,
    icon: Icon | None = None,
) -> InlineKeyboardButton:
    """Кнопка с премиум-иконкой, если она включена, и с обычным эмодзи, если нет.

    Выключенный флаг — рабочее состояние по умолчанию, а не аварийное, поэтому
    в нём подпись обязана выглядеть законченной сама по себе: запасной эмодзи
    подставляется в текст.
    """
    if icon is None:
        return InlineKeyboardButton(text=text, callback_data=callback_data, url=url)

    if not icons_enabled():
        return InlineKeyboardButton(text=f'{icon.fallback} {text}', callback_data=callback_data, url=url)

    if _NATIVE_ICON_FIELD:
        return InlineKeyboardButton(
            text=text, callback_data=callback_data, url=url, icon_custom_emoji_id=str(icon)
        )

    return InlineKeyboardButton(
        text=text, callback_data=callback_data, url=url, api_kwargs={'icon_custom_emoji_id': str(icon)}
    )


def back_button(callback_data: str = 'main_menu') -> InlineKeyboardButton:
    """Плоский возврат: один известный родитель, без истории переходов.

    Домик рисуется только у возврата в главное меню — на промежуточных экранах
    он обещал бы не тот адрес, куда кнопка ведёт.
    """
    return button('Назад', callback_data, icon=icons.HOME if callback_data == 'main_menu' else None)


def code(value: str) -> str:
    """Моноширинный блок: Telegram копирует его по нажатию, кнопка не нужна."""
    return f'<code>{html.escape(str(value))}</code>'


async def answer_query(update: Update, text: str | None = None) -> None:
    """Снять «часики» с нажатой кнопки, по желанию показав короткий тост.

    Нажатие без ответа Telegram крутит часы до собственного таймаута, поэтому
    любой выход из обработчика мимо `render_screen` читается как зависший бот.
    Повторный ответ Bot API отклоняет — обработчик мог ответить раньше.
    """
    query = update.callback_query
    if query is None:
        return

    with contextlib.suppress(BadRequest):
        await query.answer(text=text)


def screen(title: str, *, state: Iterable[str] | None = None, body: Iterable[str] | None = None) -> str:
    """Собрать текст экрана по единому шаблону.

    Заголовок, цитата с состоянием и числами, тело. Разделители — только пустые
    строки: линейки из `—` и `═` в клиенте выглядят мусором на узком экране.
    """
    blocks: list[str] = [f'<b>{html.escape(title)}</b>']

    state_lines = [line for line in (state or ()) if line]
    if state_lines:
        blocks.append('<blockquote>{}</blockquote>'.format('\n'.join(state_lines)))

    blocks.extend(block for block in (body or ()) if block)

    return '\n\n'.join(blocks)


async def render_screen(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    keyboard: InlineKeyboardMarkup | None = None,
    *,
    force_new: bool = False,
    toast: str | None = None,
) -> None:
    """Показать экран, переписав сообщение с нажатой кнопкой.

    Якорь нигде не хранится: следующее нажатие приходит от того сообщения, чью
    кнопку нажали, поэтому после отправки нового сообщения якорь смещается на
    него сам.

    `toast` — подтверждение поверх экрана. Оно нужно там, где действие удалось,
    а экран после него выглядит почти прежним: без тоста успешное нажатие
    неотличимо от несработавшего.
    """
    query = update.callback_query

    await answer_query(update, toast)

    if query is not None and not force_new:
        try:
            await query.edit_message_text(
                text=text,
                reply_markup=keyboard,
                parse_mode=PARSE_MODE,
                link_preview_options=NO_PREVIEW,
            )
            return
        except BadRequest as error:
            message = str(error).lower()
            if _UNCHANGED in message:
                return
            if not any(reason in message for reason in _UNEDITABLE):
                raise

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        reply_markup=keyboard,
        parse_mode=PARSE_MODE,
        link_preview_options=NO_PREVIEW,
    )
