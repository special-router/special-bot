from telegram import InlineKeyboardButton, InlineKeyboardMarkup


async def get_reply_markup_referral() -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text='Назад', callback_data='main_menu'),
        ],
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)
