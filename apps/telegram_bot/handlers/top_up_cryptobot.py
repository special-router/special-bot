"""Unified period → method top-up flow: card via Telegram Payments, USDT via CryptoBot."""
import asyncio
import logging
from decimal import Decimal, ROUND_UP

from django.conf import settings
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from apps.payments.cryptobot_client import create_usdt_invoice, get_usdt_rate
from apps.payments.models import CryptoBotInvoice
from apps.servers.models import TariffServer
from apps.telegram_bot.handlers.top_up_balance import (
    top_up_balance_one_month,
    top_up_balance_two_month,
    top_up_balance_three_month,
    top_up_balance_six_month,
    top_up_balance_year,
)
from apps.telegram_bot.ui import answer_query, back_button, bold, button, render_screen, screen
from apps.telegram_bot.utils import get_user, payments_enabled


logger = logging.getLogger(__name__)

_INVOICE_ERROR_TOAST = 'Ошибка создания счёта. Попробуйте позже.'
_RATE_UNAVAILABLE_TOAST = 'Курс временно недоступен. Попробуйте картой.'

# Maps months key → (count_days, bonus_percent)
_PERIOD_CONFIG: dict[int, tuple[int, int]] = {
    1:  (30,  0),
    2:  (60,  5),
    3:  (90,  10),
    6:  (180, 20),
    12: (365, 30),
}

_PERIOD_LABELS: dict[int, str] = {
    1:  '1 месяц',
    2:  '2 месяца +5%',
    3:  '3 месяца +10%',
    6:  'Полгода +20%',
    12: 'Год +30%',
}

# Dispatch table — avoids duplicating invoice-creation logic from top_up_balance.
_CARD_HANDLERS = {
    1:  top_up_balance_one_month,
    2:  top_up_balance_two_month,
    3:  top_up_balance_three_month,
    6:  top_up_balance_six_month,
    12: top_up_balance_year,
}


async def _anone() -> None:
    """Null coroutine used in asyncio.gather when the crypto branch is skipped."""
    return None


async def topup_period_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the method picker (card / USDT) for a chosen subscription period.

    Callback: ``topup_period:<months>``
    """
    query = update.callback_query
    try:
        months = int(query.data.split(':')[1])
    except (IndexError, ValueError):
        await answer_query(update)
        return

    if months not in _PERIOD_CONFIG:
        await answer_query(update)
        return

    count_days, _percent = _PERIOD_CONFIG[months]
    period_label = _PERIOD_LABELS[months]

    token = getattr(settings, 'CRYPTOBOT_TOKEN', '')
    tariff, rate = await asyncio.gather(
        TariffServer.objects.aget(),
        get_usdt_rate(token) if token else _anone(),
    )

    amount_rub = int(tariff.price * count_days)

    rows: list[list[InlineKeyboardButton]] = []

    if payments_enabled():
        rows.append([button(f'Картой — {amount_rub} ₽', f'topup_card:{months}')])

    if rate is not None:
        amount_usdt = (Decimal(amount_rub) / rate).quantize(Decimal('0.01'), rounding=ROUND_UP)
        if amount_usdt < Decimal('0.01'):
            amount_usdt = Decimal('0.01')
        rows.append([button(
            f'USDT — {amount_usdt} USDT (~{amount_rub} ₽)',
            f'topup_crypto_pay:{months}:{amount_rub}',
        )])

    rows.append([back_button('show_balance')])
    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)

    text = screen('Способ оплаты', state=[period_label, f'{amount_rub} ₽'])
    await render_screen(update, context, text, keyboard)


async def topup_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route to the existing card-payment handler for the chosen period.

    Callback: ``topup_card:<months>``
    """
    query = update.callback_query
    try:
        months = int(query.data.split(':')[1])
    except (IndexError, ValueError):
        await answer_query(update)
        return

    handler = _CARD_HANDLERS.get(months)
    if handler is None:
        await answer_query(update)
        return

    await handler(update, context)


async def topup_crypto_pay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Create a CryptoBot USDT invoice for the chosen period.

    Callback: ``topup_crypto_pay:<months>:<amount_rub>``

    ``amount_rub`` is a positive integer of roubles — validated before use.
    The exchange rate is re-fetched here, not reused from the method-picker
    screen, because the user may have waited.
    """
    query = update.callback_query
    parts = query.data.split(':')
    try:
        months = int(parts[1])
        amount_rub = int(parts[2])
        if amount_rub <= 0:
            raise ValueError('non-positive amount')
    except (IndexError, ValueError):
        await answer_query(update, 'Неверные данные. Попробуйте снова.')
        return

    rate = await get_usdt_rate(settings.CRYPTOBOT_TOKEN)
    if rate is None:
        await answer_query(update, _RATE_UNAVAILABLE_TOAST)
        return

    amount_usdt = (Decimal(amount_rub) / rate).quantize(Decimal('0.01'), rounding=ROUND_UP)
    if amount_usdt < Decimal('0.01'):
        amount_usdt = Decimal('0.01')

    user = await get_user(update)

    result = await create_usdt_invoice(
        token=settings.CRYPTOBOT_TOKEN,
        amount_usdt=str(amount_usdt),
        user_db_id=user.id,
        description=f'Пополнение баланса {amount_rub} ₽',
    )

    if result is None:
        await answer_query(update, _INVOICE_ERROR_TOAST)
        return

    await CryptoBotInvoice.objects.acreate(
        invoice_id=result['invoice_id'],
        user=user,
        amount_rub=Decimal(amount_rub),
        amount_usdt=amount_usdt,
    )

    pay_url = result.get('mini_app_invoice_url') or result.get('bot_invoice_url', '')
    period_label = _PERIOD_LABELS.get(months, f'{months} мес.')

    text = screen(
        'Оплата криптовалютой',
        state=[
            bold(period_label),
            bold(f'{amount_rub} ₽'),
            f'= {amount_usdt} USDT',
        ],
        body=['Нажмите кнопку ниже, чтобы перейти к оплате.'],
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Оплатить в CryptoBot', url=pay_url)],
        [back_button('show_balance')],
    ])
    await render_screen(update, context, text, keyboard)
