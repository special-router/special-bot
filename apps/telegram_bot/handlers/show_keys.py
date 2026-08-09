from typing import Final

from telegram import Update
from telegram.ext import ContextTypes

from apps.telegram_bot.inline_buttons.manage_keys import get_reply_markup_manage_keys
from apps.telegram_bot.utils import get_user
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN
from apps.vpn.services.subscription_delivery import get_user_access_url


VPN_KEY_INFO_TEMPLATE: Final[
    str
] = """
🔸 **{server_name}**
   Ссылка подключения (на 2 устройства, цена 7 руб/сутки):
   `{access_url}`
   Дата создания: {created_date}
"""


async def show_keys(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user: TelegramUser = await get_user(update)

    vpn_keys_info: str = ''

    # Получаем VPN ключи пользователя
    async for vpn_connection in UserVPN.objects.with_related_server().filter(user=user):
        access_url = await get_user_access_url(vpn_connection)
        vpn_keys_info += VPN_KEY_INFO_TEMPLATE.format(
            server_name=vpn_connection.server.name,
            access_url=access_url,
            created_date=vpn_connection.created_at.date(),
        )

    await context.bot.send_message(
        user.telegram_id,
        text=vpn_keys_info or 'У вас нет доступных ключей',
        parse_mode='Markdown',
        reply_markup=await get_reply_markup_manage_keys(user),
    )
