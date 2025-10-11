from typing import Final

from django.conf import settings
from telegram import Update, InputMediaPhoto
from telegram.ext import ContextTypes


FAQ_TEXT: Final[str] = """
⬆️ ИНСТРУКЦИЯ ПО ПОДКЛЮЧЕНИЮ НА КАРТИНКАХ⬆️

Для работы V*N необходимо установить приложение v2raytun на телефон и Hiddify на компьютер
Apple Ссылка (iPhone/ MacBook) - https://apps.apple.com/app/id6476628951
Android Ссылка -  https://play.google.com/store/apps/details?id=com.v2raytun.android&hl=ru

Если Вы хотите установить V*N на Ваш Компьютер, скачайте приложение Hiddify, функционал у него точно такой же

Windows ПК и Ноутбуки - https://apps.microsoft.com/detail/9PDFNL3QV2S5?hl=neutral&gl=US&ocid=pdpshare
"""

async def faq(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    media = [
        InputMediaPhoto(open(f"{settings.BASE_DIR}/apps/telegram_bot/images/1.jpg", "rb")),
        InputMediaPhoto(open(f"{settings.BASE_DIR}/apps/telegram_bot/images/2.jpg", "rb")),
        InputMediaPhoto(open(f"{settings.BASE_DIR}/apps/telegram_bot/images/3.jpg", "rb")),
        InputMediaPhoto(open(f"{settings.BASE_DIR}/apps/telegram_bot/images/4.jpg", "rb")),
        InputMediaPhoto(open(f"{settings.BASE_DIR}/apps/telegram_bot/images/5.jpg", "rb")),
        InputMediaPhoto(open(f"{settings.BASE_DIR}/apps/telegram_bot/images/6.jpg", "rb")),
    ]

    await context.bot.send_media_group(
        chat_id=update.callback_query.message.chat_id,
        media=media,
    )

    await context.bot.send_message(
        chat_id=update.callback_query.message.chat_id,
        text=FAQ_TEXT,
        disable_web_page_preview=True,
    )
