from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from apps.telegram_bot import icons
from apps.telegram_bot.ui import back_button, button


async def get_reply_markup_profile() -> InlineKeyboardMarkup:
    """Профиль — витрина счёта, поэтому оба действия по нему ведут отсюда."""
    buttons: list[list[InlineKeyboardButton]] = [
        [
            button('Пополнить', 'show_balance', icon=icons.WALLET),
            button('Подписки', 'show_keys', icon=icons.KEY),
        ],
        [
            back_button(),
        ],
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)
