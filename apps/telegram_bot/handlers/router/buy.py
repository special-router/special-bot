from telegram import Update
from telegram.ext import ContextTypes

from apps.telegram_bot.handlers.router.keyboards import (
    consent_keyboard,
    router_purchase_payment_keyboard,
)
from apps.telegram_bot.handlers.router.states import ROUTER_STATE_SHIPPING, set_router_state
from apps.telegram_bot.utils import get_user


CONSENT_TEXT = """
Покупка роутера **Special Mini**

Перед оплатой необходимо дать согласие на обработку персональных данных.

Нажимая «Согласен/а», вы соглашаетесь с политикой обработки данных.
"""

BUY_TEXT = """
🛒 **Купить роутер Special Mini**

Стоимость:
• **12 000 RUB** — оплата через ЮKassa
• **120 USDT** — оплата через CryptoBot

После оплаты введите данные для доставки по РФ/СНГ.
"""


async def router_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await get_user(update)
    await context.bot.send_message(user.telegram_id, text=CONSENT_TEXT, parse_mode='Markdown', reply_markup=consent_keyboard())


async def router_buy_consent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await get_user(update)
    await context.bot.send_message(
        user.telegram_id,
        text=BUY_TEXT,
        parse_mode='Markdown',
        reply_markup=router_purchase_payment_keyboard(),
    )


async def router_buy_paid_shipping_prompt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user=None,
) -> None:
    if user is None:
        user = await get_user(update)
    set_router_state(context, ROUTER_STATE_SHIPPING)
    await context.bot.send_message(
        user.telegram_id,
        text=(
            '✅ Оплата получена!\n\n'
            'Введите данные для отправки (одним сообщением):\n'
            '• ФИО\n'
            '• Телефон\n'
            '• Служба доставки\n'
            '• Адрес\n\n'
            'Заказы по РФ/СНГ. Данные проверяет @Special_Wifi_Official, '
            'трек-номер пришлём в бот.'
        ),
    )
