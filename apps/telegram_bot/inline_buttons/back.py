from telegram import InlineKeyboardMarkup

from apps.telegram_bot.ui import back_button


async def get_reply_markup_back(callback_data: str = 'main_menu') -> InlineKeyboardMarkup:
    """Клавиатура экранов, у которых нет собственных действий.

    Раньше у каждого такого экрана был свой одинаковый модуль кнопок; отличались
    они только именем файла.
    """
    return InlineKeyboardMarkup(inline_keyboard=[[back_button(callback_data)]])
