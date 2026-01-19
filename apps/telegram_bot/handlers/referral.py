from typing import Final

from django.conf import settings
from telegram import Update
from telegram.ext import ContextTypes

from apps.telegram_bot.inline_buttons.referral import get_reply_markup_referral
from apps.telegram_bot.utils import get_user
from apps.users.models import TelegramUser


REFERRAL_TEXT: Final[
    str
] = """
Приглашайте друзей и зарабатывайте 30% с каждой их покупки.

— Вы делитесь своей реферальной ссылкой
— Друг оплачивает VPN
— 30% от всех его платежей навсегда начисляются вам на баланс

Ваша реферальная ссылка: {}
"""


async def referral(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user: TelegramUser = await get_user(update)
    await context.bot.send_message(
        chat_id=update.callback_query.message.chat_id,
        text=REFERRAL_TEXT.format(f'{settings.BOT_LINK}?start={user.telegram_id}'),
        reply_markup=await get_reply_markup_referral(),
    )
