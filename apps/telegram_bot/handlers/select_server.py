from typing import Final

from asgiref.sync import sync_to_async
from django.db import transaction
from telegram import Update, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from apps.servers.models import Server
from apps.servers.vpn_client import APIVPNClient
from apps.telegram_bot.handlers.balance import show_balance
from apps.users.models import TelegramUser
from apps.telegram_bot.utils import get_user
from apps.vpn.models import UserVPN


async def select_server(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user: TelegramUser = await get_user(update)
    server_id: int = int(update.callback_query.data.split(":")[1])
    server: Server = await Server.objects.with_related_tariffs().aget(id=server_id)

    # отправить пользователю сообщение о том, что у него нет баланса (просто инфу о балансе вывести)
    if user.balance < server.tariff.price:
        return await show_balance(update, context)

    # переделать на get_or_create
    user_vpn, is_created = await UserVPN.objects.aget_or_create(
        user=user, server=server,
    )

    user_vpn = await UserVPN.objects.with_related_user().with_related_server().aget(id=user_vpn.id)

    if is_created:
        await APIVPNClient(server).add_user(user_vpn)
        user_vpn.vpn_key = await APIVPNClient(server).get_key(user_vpn)
        await user_vpn.asave()

    await context.bot.send_message(chat_id=update.callback_query.message.chat_id, text=f'Ваш ключ:{user_vpn.vpn_key}')
