from typing import Final

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from apps.servers.models import TariffServer
from apps.telegram_bot.inline_buttons.balance import get_reply_markup_balance
from apps.telegram_bot.ui import render_screen, screen
from apps.telegram_bot.utils import get_user
from apps.users.models import TelegramUser


TOP_UP_HINT: Final[str] = 'Списание идёт посуточно, пока подписка активна. Выберите срок пополнения.'


async def build_balance_screen(user: TelegramUser, *, notice: str | None = None) -> tuple[str, InlineKeyboardMarkup]:
    """Цена берётся из тарифа, а не из текста: раньше «7 руб/сутки» было вписано
    в два шаблона и расходилось бы с тарифом при первой же его правке."""
    tariff: TariffServer | None = await TariffServer.objects.afirst()

    state = [f'Баланс: {user.balance} руб.']
    if tariff is not None:
        state.append(f'Подписка: {tariff.price} руб. в сутки')

    text = screen('Оплата', state=state, body=[notice, TOP_UP_HINT])

    return text, await get_reply_markup_balance(user)


async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user: TelegramUser = await get_user(update)
    text, keyboard = await build_balance_screen(user)
    await render_screen(update, context, text, keyboard)
