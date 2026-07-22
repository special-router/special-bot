import json

from django.conf import settings
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Update
from telegram.ext import ContextTypes

from apps.payments.choices import InvoiceStatusChoices, PaymentMethodChoices
from apps.payments.cryptobot.client import CryptoBotClient
from apps.payments.models import Invoice
from apps.subscriptions.constants import (
    ROUTER_ACTIVATION_FIRST_MONTH_RUB,
    ROUTER_PURCHASE_PRICE_RUB,
    ROUTER_PURCHASE_PRICE_USDT,
    ROUTER_TARIFFS_RUB,
    ROUTER_TARIFFS_USDT,
)
from apps.subscriptions.models import RouterDevice, RouterOrder
from apps.telegram_bot.utils import get_user
from apps.users.models import TelegramUser


def _router_payload(order_type: str, **kwargs) -> str:
    return json.dumps({'type': 'router', 'order_type': order_type, **kwargs})


def _amount_for_tariff(currency: str, months: int) -> float:
    if currency == 'RUB':
        return float(ROUTER_TARIFFS_RUB[months])
    return float(ROUTER_TARIFFS_USDT[months])


async def send_router_telegram_invoice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: TelegramUser,
    title: str,
    amount_rub: float,
    payload: str,
) -> None:
    amount_kopecks = int(amount_rub * 100)
    await context.bot.send_invoice(
        chat_id=update.effective_chat.id,
        title=title,
        description=title,
        payload=payload,
        provider_token=settings.YOUMONEY_TOKEN,
        currency='RUB',
        prices=[LabeledPrice('Цена', amount_kopecks)],
    )


async def send_router_cryptobot_invoice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: TelegramUser,
    title: str,
    amount: float,
    payload_data: dict,
    currency: str = 'RUB',
) -> None:
    if not settings.CRYPTOBOT_TOKEN:
        await update.callback_query.answer(text='CryptoBot не настроен')
        return

    payload_str = json.dumps(payload_data)
    client = CryptoBotClient()

    if currency == 'USDT':
        result = await client.create_invoice(
            amount_rub=amount,
            description=title,
            payload=payload_str,
            currency_type='crypto',
            asset='USDT',
        )
    else:
        result = await client.create_invoice(
            amount_rub=amount,
            description=title,
            payload=payload_str,
        )

    await Invoice.objects.acreate(
        user=user,
        external_id=str(result['invoice_id']),
        product_line='SUBSCRIPTION',
        amount=amount,
        currency=currency,
        payment_method=PaymentMethodChoices.CRYPTOBOT,
        payload=payload_data,
        status=InvoiceStatusChoices.PENDING,
    )

    pay_url = result.get('bot_invoice_url') or result.get('pay_url')
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(text='Оплатить в CryptoBot', url=pay_url)]])
    await context.bot.send_message(user.telegram_id, text=f'{title}\n\nОплатите счёт:', reply_markup=keyboard)
    await update.callback_query.answer()


async def router_buy_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await get_user(update)
    currency = update.callback_query.data.split(':')[1]

    if currency == 'RUB':
        amount = ROUTER_PURCHASE_PRICE_RUB
        payload = _router_payload('ROUTER_PURCHASE', currency='RUB')
        await RouterOrder.objects.acreate(
            user=user,
            order_type=RouterOrder.OrderType.ROUTER_PURCHASE,
            amount=amount,
            currency='RUB',
            payment_payload=json.loads(payload),
        )
        await send_router_telegram_invoice(
            update,
            context,
            user,
            'Special Mini — 12 000 RUB',
            amount,
            payload,
        )
    else:
        amount = ROUTER_PURCHASE_PRICE_USDT
        payload_data = {'type': 'router', 'order_type': 'ROUTER_PURCHASE', 'currency': 'USDT'}
        await RouterOrder.objects.acreate(
            user=user,
            order_type=RouterOrder.OrderType.ROUTER_PURCHASE,
            amount=amount,
            currency='USDT',
            payment_payload=payload_data,
        )
        await send_router_cryptobot_invoice(
            update,
            context,
            user,
            'Special Mini — 120 USDT',
            amount,
            payload_data,
            currency='USDT',
        )


async def router_activation_pay_tg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await get_user(update)
    device_id = int(update.callback_query.data.split(':')[1])
    device = await RouterDevice.objects.aget(id=device_id, owner=user)

    payload = _router_payload('ACTIVATION', device_id=device_id)
    await send_router_telegram_invoice(
        update,
        context,
        user,
        f'Первая подписка {device.display_id} — 500 ₽',
        ROUTER_ACTIVATION_FIRST_MONTH_RUB,
        payload,
    )


async def router_activation_pay_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await get_user(update)
    device_id = int(update.callback_query.data.split(':')[1])
    device = await RouterDevice.objects.aget(id=device_id, owner=user)

    payload_data = {'type': 'router', 'order_type': 'ACTIVATION', 'device_id': device_id}
    await send_router_cryptobot_invoice(
        update,
        context,
        user,
        f'Первая подписка {device.display_id} — 500 ₽',
        ROUTER_ACTIVATION_FIRST_MONTH_RUB,
        payload_data,
    )


async def router_sub_pay_tg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await get_user(update)
    parts = update.callback_query.data.split(':')
    device_id, currency, months = int(parts[1]), parts[2], int(parts[3])
    device = await RouterDevice.objects.aget(id=device_id, owner=user)

    if currency != 'RUB':
        await update.callback_query.answer(text='ЮKassa доступна только для RUB')
        return

    amount = _amount_for_tariff(currency, months)
    payload = _router_payload('SUBSCRIPTION', device_id=device_id, months=months, currency=currency)
    await send_router_telegram_invoice(
        update,
        context,
        user,
        f'Подписка {device.display_id} — {months} мес',
        amount,
        payload,
    )


async def router_sub_pay_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await get_user(update)
    parts = update.callback_query.data.split(':')
    device_id, currency, months = int(parts[1]), parts[2], int(parts[3])
    device = await RouterDevice.objects.aget(id=device_id, owner=user)

    amount = _amount_for_tariff(currency, months)
    payload_data = {
        'type': 'router',
        'order_type': 'SUBSCRIPTION',
        'device_id': device_id,
        'months': months,
        'currency': currency,
    }
    title = f'Подписка {device.display_id} — {months} мес'
    await send_router_cryptobot_invoice(
        update,
        context,
        user,
        title,
        amount,
        payload_data,
        currency=currency,
    )
