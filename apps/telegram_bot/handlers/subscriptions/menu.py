from telegram import Update
from telegram.ext import ContextTypes

from apps.telegram_bot.handlers.router.menu import special_router_menu


async def subscriptions_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Legacy callback — redirect to Special Router menu."""
    await special_router_menu(update, context)
