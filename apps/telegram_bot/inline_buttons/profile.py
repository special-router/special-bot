from telegram import InlineKeyboardButton, InlineKeyboardMarkup


async def get_reply_markup_profile() -> InlineKeyboardMarkup:
    """Создает клавиатуру для профиля пользователя"""
    buttons: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text='🌐 Серверы', callback_data='list_servers'),
        ],
        [
            InlineKeyboardButton(text='👁 Инструкция', callback_data='faq'),
            InlineKeyboardButton(text='👨🏻‍🔧 Тех.поддержка', url='https://t.me/Special_Wifi_Official'),
        ],
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)
