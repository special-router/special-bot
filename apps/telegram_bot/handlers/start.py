from typing import Final

from telegram import Update
from telegram.ext import ContextTypes

from apps.telegram_bot.inline_buttons.server_list import get_reply_markup_list_servers


HELLO_TEXT: Final[str] = (
    """
🌐 Добро пожаловать в наш VPN-сервис! 🌐
Наш бот поможет вам приобрести подписку на VPN,
 чтобы вы могли безопасно и анонимно пользоваться интернетом.

🔒 Ваша конфиденциальность — наш приоритет. С нашим VPN вы получите:
✅ Защиту ваших данных
✅ Высокую скорость соединения
✅ Поддержку 24/7

Чтобы начать, выберите один из доступных серверов

Спасибо, что выбрали нас! 🚀
"""
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELLO_TEXT, reply_markup=await get_reply_markup_list_servers())
