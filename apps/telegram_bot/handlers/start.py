from telegram import Update
from telegram.ext import ContextTypes

from apps.telegram_bot.handlers.main_menu import build_main_menu_screen
from apps.telegram_bot.ui import render_screen
from apps.telegram_bot.utils import get_referral_user, get_user
from apps.users.models import TelegramUser


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Единственная точка, где бот заводит новое сообщение по своей воле:
    редактировать здесь нечего, беседа только начинается."""
    referral_user: TelegramUser | None = await get_referral_user(update)
    user: TelegramUser = await get_user(update, referral_user)

    text, keyboard = await build_main_menu_screen(user, greeting=True)
    await render_screen(update, context, text, keyboard, force_new=True)
