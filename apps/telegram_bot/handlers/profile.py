from typing import Final

from asgiref.sync import sync_to_async
from telegram import Update
from telegram.ext import ContextTypes

from apps.telegram_bot.inline_buttons.profile import get_reply_markup_profile
from apps.telegram_bot.utils import get_user
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN
from apps.vpn.services.subscription_delivery import get_user_access_url


PROFILE_TEXT_TEMPLATE: Final[
    str
] = """
👤 **Мой профиль (id: `{user_telegram_id}`)**

💰 **Баланс:** {balance} руб.

🔑 **VPN ключи:**
{vpn_keys_info}

📊 **Статистика:**
• Дата регистрации: {registration_date}
• Всего VPN подключений: {vpn_count}
"""

VPN_KEY_INFO_TEMPLATE: Final[
    str
] = """
🔸 **{server_name}**
   Ссылка подключения (на 2 устройства, цена 7 руб/сутки):
   `{access_url}`
   Статус: {status}
"""


async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать профиль пользователя с балансом, VPN ключами и подписками"""
    user: TelegramUser = await get_user(update)

    # Получаем пользователя с аннотированным балансом
    user_with_balance = await TelegramUser.objects.annotate_balance().aget(id=user.id)

    # Получаем VPN ключи пользователя
    vpn_connections = await sync_to_async(list)(UserVPN.objects.with_related_server().filter(user=user))

    # Формируем информацию о VPN ключах
    vpn_keys_info = ''
    if vpn_connections:
        for vpn in vpn_connections:
            status = '✅ Активен' if vpn.enabled else '❌ Неактивен'
            access_url = await get_user_access_url(vpn)
            vpn_keys_info += VPN_KEY_INFO_TEMPLATE.format(
                server_name=vpn.server.name, access_url=access_url, status=status
            )
    else:
        vpn_keys_info = '❌ Нет активных VPN ключей'

    # Формируем текст профиля
    profile_text = PROFILE_TEXT_TEMPLATE.format(
        balance=user_with_balance.balance,
        vpn_keys_info=vpn_keys_info,
        registration_date=user.created_at.strftime('%d.%m.%Y'),
        vpn_count=len(vpn_connections),
        user_telegram_id=user.telegram_id,
    )

    await context.bot.send_message(
        user.telegram_id,
        text=profile_text,
        parse_mode='Markdown',
        reply_markup=await get_reply_markup_profile(),
    )
