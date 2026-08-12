import random

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from apps.telegram_bot import icons
from apps.telegram_bot.ui import back_button, button
from apps.users.models import TelegramUser


async def get_reply_markup_manage_keys(user: TelegramUser) -> InlineKeyboardMarkup:
    """Одноразовое число в `add_key` — защита от повторного нажатия, см. handlers/add_key.py."""
    buttons: list[list[InlineKeyboardButton]] = [
        [
            button('Добавить', f'add_key:{random.randint(10000000, 999999999)}', icon=icons.KEY),
            button('Удалить', 'show_keys_for_remove', icon=icons.TRASH),
        ],
        [
            button('Привязать устройство', 'bind_device', icon=icons.LINK),
            button('Сбросить', 'reset_devices', icon=icons.REFRESH),
        ],
        [
            back_button(),
        ],
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)
