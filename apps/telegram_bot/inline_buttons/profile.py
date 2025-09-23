from telegram import InlineKeyboardButton, InlineKeyboardMarkup


async def get_reply_markup_profile() -> InlineKeyboardMarkup:
    """Создает клавиатуру для профиля пользователя"""
    buttons: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text='Назад', callback_data='main_menu'),
        ],
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)
