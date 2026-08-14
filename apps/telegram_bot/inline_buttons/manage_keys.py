import random

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from apps.telegram_bot import icons
from apps.telegram_bot.ui import back_button, button


async def get_reply_markup_manage_keys(*, connected: bool = False) -> InlineKeyboardMarkup:
    """Одноразовое число в `add_key` — защита от повторного нажатия, см. handlers/add_key.py.

    Числом подписок пользователь не управляет: она у аккаунта одна, и кнопка
    заводит её ровно тогда, когда её нет или её отключило списание. Пока она
    работает, кнопки нет вовсе — раньше на её месте стояло «Добавить», каждое
    нажатие которого списывало сутки и возвращало ту же подписку.

    Привязки здесь нет намеренно: устройство привязывается само, когда клиент
    забирает подписку. Управление местами и поимённая отвязка живут на своём
    экране, потому что там у каждого устройства своя кнопка.
    """
    buttons: list[list[InlineKeyboardButton]] = []

    if not connected:
        buttons += [[button('Подключить', f'add_key:{random.randint(10000000, 999999999)}', icon=icons.KEY)]]

    buttons += [
        [button('Устройства', 'show_devices', icon=icons.PROFILE)],
        [back_button()],
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)
