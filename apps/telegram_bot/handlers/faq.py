from typing import Final

from telegram import Update
from telegram.ext import ContextTypes


HELP_TEXT: Final[
    str
] = """
⚡Инструкция по подключению в этой ссылке:
https://telegra.ph/Special-VPN-Instrukciya-04-15

⚡Также наш канал:
https://t.me/special_wifi
"""


async def faq(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await context.bot.send_message(
        chat_id=update.callback_query.message.chat_id,
        text=HELP_TEXT,
    )
