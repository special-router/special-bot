from django.conf import settings
from telegram import Update, InputMediaPhoto
from telegram.ext import ContextTypes


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
