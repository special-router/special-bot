from typing import Final

from telegram import Update
from telegram.ext import ContextTypes

from apps.telegram_bot.inline_buttons.start import get_reply_markup_main_menu
from apps.telegram_bot.utils import get_user
from apps.users.models import TelegramUser


async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user: TelegramUser = await get_user(update)
    await context.bot.send_message(
        chat_id=update.callback_query.message.chat_id,
        text='Меню:', reply_markup=await get_reply_markup_main_menu()
    )
