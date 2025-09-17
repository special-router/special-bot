from telegram import Update
from telegram.ext import ContextTypes

from apps.telegram_bot.inline_buttons.server_list import get_reply_markup_list_servers
from apps.telegram_bot.utils import get_user
from apps.users.models import TelegramUser


async def list_servers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user: TelegramUser = await get_user(update)

    await context.bot.send_message(
        user.telegram_id,
        text=f"Доступные сервера",
        reply_markup=await get_reply_markup_list_servers(),
    )