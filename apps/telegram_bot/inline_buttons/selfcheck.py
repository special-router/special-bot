from telegram import InlineKeyboardMarkup

from apps.telegram_bot.ui import back_button


async def get_reply_markup_selfcheck() -> InlineKeyboardMarkup:
    """Только назад — экран ничего не меняет, его незачем перегружать кнопками."""
    return InlineKeyboardMarkup(inline_keyboard=[[back_button('profile')]])
