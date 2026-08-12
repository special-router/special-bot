from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


async def get_reply_markup_remove_keys(user: TelegramUser) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []

    async for vpn_connection in UserVPN.objects.with_related_server().filter_by_user(user_id=user.id):
        buttons += [
            [
                InlineKeyboardButton(
                    text=f'Удалить подписку от {vpn_connection.created_at.date()}',
                    callback_data=f'remove_key:{vpn_connection.id}',
                ),
            ]
        ]

    buttons += [
        [
            InlineKeyboardButton(text='Назад', callback_data='show_keys'),
        ],
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)
