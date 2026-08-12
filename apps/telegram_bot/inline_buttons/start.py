from django.conf import settings
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from apps.telegram_bot import icons
from apps.telegram_bot.ui import button


SUPPORT_URL = 'https://t.me/Special_Wifi_Official'
PARTNERSHIP_URL = 'https://t.me/nu_magich'


def _support_button() -> InlineKeyboardButton:
    """Обращения внутри бота — только когда есть куда их складывать.

    Без чата операторов тикет некуда положить, поэтому кнопка остаётся тем же
    внешним переходом, что и раньше: пользователь не должен упереться в
    поддержку, которой нет.
    """
    if settings.SUPPORT_CHAT_ID:
        return button('Поддержка', 'support_open', icon=icons.PEOPLE)
    return button('Поддержка', url=SUPPORT_URL, icon=icons.PEOPLE)


async def get_reply_markup_main_menu() -> InlineKeyboardMarkup:
    """Главное меню: подписки первой строкой, всё остальное — парами."""
    buttons: list[list[InlineKeyboardButton]] = [
        [
            button('Подписки', 'show_keys', icon=icons.KEY),
        ],
        [
            button('Оплата', 'show_balance', icon=icons.WALLET),
            button('Профиль', 'profile', icon=icons.PROFILE),
        ],
        [
            button('Инструкция', 'faq', icon=icons.BOOK),
            button('Друзьям', 'referral', icon=icons.GIFT),
        ],
        [
            _support_button(),
            button('Сотрудничество', url=PARTNERSHIP_URL, icon=icons.MONEY),
        ],
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)
