from asgiref.sync import sync_to_async
from telegram import Update
from telegram.ext import ContextTypes
from typing import Final

from telegram import Update, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from apps.servers.managers import ServerQuerySet
from apps.servers.models import Server
from apps.telegram_bot.inline_buttons.server_list import get_reply_markup_list_servers
from apps.users.models import TelegramUser
from apps.telegram_bot.utils import get_user
from apps.telegram_bot.handlers.top_up_balance import top_up_balance
from apps.vpn.models import UserVPN

# не используется
async def info_server(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user: TelegramUser = await get_user(update)
    server_id: int = int(update.callback_query.data.split(":")[1])
    server: Server = await Server.objects.with_related_tariffs().aget(id=server_id)

    user_vpn: UserVPN = await sync_to_async(
        UserVPN.objects.filter_by_user(user.id).filter_by_server
    )(server_id)

    await update.message.reply_text(HELLO_TEXT)


