from decimal import Decimal, ROUND_UP

from django.conf import settings
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from apps.payments.cryptobot_client import create_usdt_invoice
from apps.payments.models import CryptoBotInvoice
from apps.telegram_bot.ui import answer_query, back_button, bold, button, render_screen, screen
from apps.telegram_bot.utils import get_user
from apps.users.models import TelegramUser


_PRESET_AMOUNTS = [100, 300, 500, 1000, 3000]

_INVOICE_ERROR_TOAST = 'Ошибка создания счёта. Попробуйте позже.'


async def show_crypto_topup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = [
        [button(f'{amount} ₽', f'crypto_topup:{amount}') for amount in pair]
        for pair in _chunked(_PRESET_AMOUNTS, 2)
    ]
    rows.append([back_button('show_balance')])
    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
    text = screen('Пополнение криптовалютой', body=['Выберите сумму пополнения в рублях:'])
    await render_screen(update, context, text, keyboard)


async def crypto_amount_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    amount_rub = Decimal(query.data.split(':')[1])

    rate = getattr(settings, 'CRYPTOBOT_USDT_RATE', Decimal('90'))
    amount_usdt = (amount_rub / Decimal(str(rate))).quantize(Decimal('0.01'), rounding=ROUND_UP)
    if amount_usdt < Decimal('0.01'):
        amount_usdt = Decimal('0.01')

    user: TelegramUser = await get_user(update)

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
        amount_rub=amount_rub,
        amount_usdt=amount_usdt,
    )

    pay_url = result.get('mini_app_invoice_url') or result.get('bot_invoice_url', '')

    text = screen(
        'Оплата криптовалютой',
        state=[
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


def _chunked(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]
