from typing import Final

from telegram import Update
from telegram.ext import ContextTypes

from apps.telegram_bot.inline_buttons.server_list import get_reply_markup_list_servers
from apps.telegram_bot.utils import get_user
from apps.users.models import TelegramUser

HELLO_TEXT: Final[str] = (
    """
🌎 Добро пожаловать!

Наш сервис — это ваша безопасность и свобода в интернете.

✨ С нами вы получаете:
✅ Защиту и приватность
✅ Очень высокую скорость
✅ Доступ к YouTube, TikTok и ChatGPT и др.
✅ Поддержку 24/7

Выберите сервер и будьте онлайн без ограничений 🚀

♥️ Спасибо, что выбрали нас!
"""
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user: TelegramUser = await get_user(update)
    await update.message.reply_text(HELLO_TEXT, reply_markup=await get_reply_markup_list_servers())
