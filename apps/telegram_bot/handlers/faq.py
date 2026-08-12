import contextlib
import html
from typing import Final

from django.conf import settings
from telegram import InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.ext import ContextTypes

from apps.telegram_bot.inline_buttons.back import get_reply_markup_back
from apps.telegram_bot.ui import render_screen, screen


APPS_BODY: Final[str] = (
    'iPhone и MacBook: https://apps.apple.com/app/id6476628951\n'
    'Android: https://play.google.com/store/apps/details?id=com.v2raytun.android&hl=ru\n'
    'Windows: https://apps.microsoft.com/detail/9PDFNL3QV2S5?hl=neutral&gl=US&ocid=pdpshare'
)

STEPS_BODY: Final[str] = (
    'Установите приложение, откройте раздел «Подписки», нажмите на ссылку — она скопируется — '
    'и вставьте её в приложение. Тот же порядок показан на картинках выше.'
)

SCREENSHOTS: Final[tuple[str, ...]] = ('1.jpg', '2.jpg', '3.jpg', '4.jpg', '5.jpg', '6.jpg')


async def build_faq_screen() -> tuple[str, InlineKeyboardMarkup]:
    # В ссылках магазинов есть `&`, а сообщения уходят в режиме HTML: без
    # экранирования Telegram разберёт хвост параметров как сущность.
    text = screen(
        'Инструкция',
        state=['На телефоне — v2raytun, на компьютере — Hiddify. Возможности у них одинаковые.'],
        body=[html.escape(APPS_BODY), STEPS_BODY],
    )

    return text, await get_reply_markup_back()


async def faq(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Альбом отредактировать нельзя, поэтому текст уходит новым сообщением под
    ним и сам становится следующим якорем."""
    with contextlib.ExitStack() as photos:
        media = [
            InputMediaPhoto(photos.enter_context(open(f'{settings.BASE_DIR}/apps/telegram_bot/images/{name}', 'rb')))
            for name in SCREENSHOTS
        ]
        await context.bot.send_media_group(chat_id=update.effective_chat.id, media=media)

    text, keyboard = await build_faq_screen()
    await render_screen(update, context, text, keyboard, force_new=True)
