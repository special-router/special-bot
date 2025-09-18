from telegram import Update
from telegram.ext import ContextTypes

from apps.servers.models import Server
from apps.servers.vpn_client import APIVPNClient
from apps.telegram_bot.handlers.balance import show_balance
from apps.telegram_bot.utils import get_user
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN
from apps.vpn.services.add_vpn_to_user import add_vpn_to_user


async def select_server(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user: TelegramUser = await get_user(update)
    server_id: int = int(update.callback_query.data.split(':')[1])
    server: Server = await Server.objects.with_related_tariffs().aget(id=server_id)

    # отправить пользователю сообщение о том, что у него нет баланса (просто инфу о балансе вывести)
    if user.balance < server.tariff.price:
        return await show_balance(update, context)

    # переделать на get_or_create
    user_vpn: UserVPN = add_vpn_to_user(user, server)

    await context.bot.send_message(
        chat_id=update.callback_query.message.chat_id,
        text=f"Ваш ключ:```\n{user_vpn.vpn_key}\n```",
        parse_mode='MARKDOWN',
    )
