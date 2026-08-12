import random

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from apps.users.models import TelegramUser


async def get_reply_markup_manage_keys(user: TelegramUser) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text='➕Добавить подписку', callback_data=f'add_key:{random.randint(10000000, 999999999)}'
            ),
            InlineKeyboardButton(text='❌Удалить подписку', callback_data='show_keys_for_remove'),
        ],
        [
            InlineKeyboardButton(text='📱Привязать устройство', callback_data='bind_device'),
        ],
        [
            InlineKeyboardButton(text='♻️Сбросить устройства', callback_data='reset_devices'),
        ],
        [
            InlineKeyboardButton(text='Назад', callback_data='main_menu'),
        ],
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)
