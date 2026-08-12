from typing import Final

from django.conf import settings
from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from apps.telegram_bot.inline_buttons.back import get_reply_markup_back
from apps.telegram_bot.ui import code, render_screen, screen
from apps.telegram_bot.utils import get_user
from apps.users.models import TelegramUser


REFERRAL_TERMS: Final[str] = 'Друг оплачивает подписку — 30% от каждого его платежа навсегда идут на ваш баланс.'


async def build_referral_screen(user: TelegramUser) -> tuple[str, InlineKeyboardMarkup]:
    referral_link = f'{settings.BOT_LINK}?start={user.telegram_id}'

    text = screen(
        'Друзьям',
        state=[REFERRAL_TERMS],
        body=[f'Ваша ссылка:\n{code(referral_link)}'],
    )

    return text, await get_reply_markup_back()


async def referral(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user: TelegramUser = await get_user(update)
    text, keyboard = await build_referral_screen(user)
    await render_screen(update, context, text, keyboard)
