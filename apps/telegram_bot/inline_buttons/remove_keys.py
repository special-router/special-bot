from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from apps.telegram_bot import icons
from apps.telegram_bot.ui import back_button, button
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


async def get_reply_markup_remove_keys(user: TelegramUser) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []

    async for vpn_connection in UserVPN.objects.with_related_server().filter_by_user(user_id=user.id):
        buttons += [
            [
                # Дата, а не имя сервера: удаляют подписку целиком, со всеми
                # её странами, и назвать кнопку одной из них значит обещать,
                # что остальные останутся.
                button(
                    f'Подписка от {vpn_connection.created_at.strftime("%d.%m.%Y")}',
                    f'remove_key:{vpn_connection.id}',
                    icon=icons.TRASH,
                ),
            ]
        ]

    buttons += [
        [
            back_button('show_keys'),
        ],
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)
