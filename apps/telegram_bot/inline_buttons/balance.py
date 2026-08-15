from django.conf import settings
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from apps.payments.choices import TransactionSourceChoices
from apps.payments.models import Transaction
from apps.telegram_bot import icons
from apps.telegram_bot.ui import back_button, button
from apps.telegram_bot.utils import payments_enabled
from apps.users.models import TelegramUser


async def get_reply_markup_balance(user: TelegramUser) -> InlineKeyboardMarkup:
    """Периоды пополнения по два в ряд: выбор периода ведёт к экрану метода оплаты.

    Без платёжного провайдера и без CryptoBot периоды не рисуются вовсе.
    Промо-начисление провайдера не касается и остаётся.
    """
    buttons: list[list[InlineKeyboardButton]] = []

    if payments_enabled() or getattr(settings, 'CRYPTOBOT_TOKEN', ''):
        buttons += [
            [
                button('1 месяц', 'topup_period:1'),
                button('2 месяца +5%', 'topup_period:2'),
            ],
            [
                button('3 месяца +10%', 'topup_period:3'),
                button('Полгода +20%', 'topup_period:6'),
            ],
            [
                button('Год +30%', 'topup_period:12'),
            ],
        ]

    if not (
        await Transaction.objects.filter_by_user(
            user_id=user.id,
        )
        .filter_by_source(
            source=TransactionSourceChoices.PROMO,
        )
        .aexists()
    ):
        buttons.insert(0, [button('Бесплатно 7 дней', 'top_up_balance_promo', icon=icons.CELEBRATION)])

    buttons.append([back_button()])

    return InlineKeyboardMarkup(inline_keyboard=buttons)
