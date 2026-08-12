from typing import Final

from asgiref.sync import sync_to_async
from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from apps.analytics.funnel import balance_screen_shown
from apps.servers.models import TariffServer
from apps.telegram_bot.inline_buttons.balance import get_reply_markup_balance
from apps.telegram_bot.ui import render_screen, screen
from apps.telegram_bot.utils import get_user, payments_enabled
from apps.users.models import TelegramUser


TOP_UP_HINT: Final[str] = 'Списание идёт посуточно, пока подписка активна. Выберите срок пополнения.'

# Пустой экран без объяснения читается как поломка. Причина названа прямо, и
# оставшийся баланс продолжает работать — это и сказано.
TOP_UP_UNAVAILABLE: Final[str] = (
    'Пополнение временно недоступно: платёжный провайдер не подключён. '
    'Списание с уже имеющегося баланса идёт как обычно.'
)


async def build_balance_screen(user: TelegramUser, *, notice: str | None = None) -> tuple[str, InlineKeyboardMarkup]:
    """Цена берётся из тарифа, а не из текста: раньше «7 руб/сутки» было вписано
    в два шаблона и расходилось бы с тарифом при первой же его правке."""
    tariff: TariffServer | None = await TariffServer.objects.afirst()

    state = [f'Баланс: {user.balance} руб.']
    if tariff is not None:
        state.append(f'Подписка: {tariff.price} руб. в сутки')

    hint = TOP_UP_HINT if payments_enabled() else TOP_UP_UNAVAILABLE
    text = screen('Оплата', state=state, body=[notice, hint])

    return text, await get_reply_markup_balance(user)


async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user: TelegramUser = await get_user(update)
    text, keyboard = await build_balance_screen(user)
    await render_screen(update, context, text, keyboard)
    # Первый шаг воронки записывается после отрисовки: экран не должен ждать
    # аналитику, а сама запись свои ошибки гасит и вернуть их сюда не может.
    await sync_to_async(balance_screen_shown)(user.id)
