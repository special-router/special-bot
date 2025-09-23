

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from apps.users.models import TelegramUser


async def get_reply_markup_manage_keys(user: TelegramUser) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text='Добавить ключ', callback_data='add_key'),
            InlineKeyboardButton(text='Удалить ключ', callback_data='show_keys_for_remove'),

        ],
        [
            InlineKeyboardButton(text='Назад', callback_data='main_menu'),
        ],
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)
