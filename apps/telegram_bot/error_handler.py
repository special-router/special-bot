"""Единственное место, где сбой обработчика превращается в ответ пользователю.

Без него Application пишет исключение в свой лог и на этом останавливается: на
экране остаётся крутящаяся кнопка, и пользователь не отличает отказ от
задержки. Пустой `YOUMONEY_TOKEN` три недели ронял пополнение именно так —
`Payment_provider_invalid` был виден только в логе.
"""

from __future__ import annotations

import contextlib
import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from apps.telegram_bot.ui import answer_query


logger = logging.getLogger(__name__)

# Пользователю нужны факт отказа и следующий шаг. Текст исключения выдал бы
# внутреннее устройство бота и всё равно ничего ему не объяснил.
FAILURE_TEXT = 'Не удалось выполнить действие. Сбой записан, попробуйте повторить через минуту.'


def describe(update: object) -> str:
    """Опознать нажатие в логе, не записывая туда переписку.

    В лог идут идентификаторы и `callback_data` — по ним нажатие находится в
    истории апдейтов; текст сообщения пользователя не идёт.
    """
    if not isinstance(update, Update):
        return 'update=unknown'

    parts = [f'update_id={update.update_id}']
    if update.effective_user is not None:
        parts.append(f'telegram_id={update.effective_user.id}')
    if update.callback_query is not None:
        parts.append(f'callback_data={update.callback_query.data}')
    elif update.message is not None:
        parts.append('source=message')

    return ' '.join(parts)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Записать сбой и ответить пользователю вместо молчания."""
    logger.error('telegram handler failed: %s', describe(update), exc_info=context.error)

    if not isinstance(update, Update):
        return

    # Ответ на сбой не должен порождать второй сбой: сеть уже могла отвалиться,
    # а нажатие — устареть, и тогда обработчик ошибок вызвал бы сам себя.
    with contextlib.suppress(TelegramError):
        await answer_query(update, FAILURE_TEXT)

    # Тост живёт несколько секунд и гасится поверх неизменившегося экрана,
    # поэтому отдельным сообщением остаётся то, что пользователь потом найдёт.
    chat = update.effective_chat
    if chat is None:
        return

    with contextlib.suppress(TelegramError):
        await context.bot.send_message(chat_id=chat.id, text=FAILURE_TEXT)
