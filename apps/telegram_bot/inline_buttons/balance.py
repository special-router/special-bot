from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from apps.payments.choices import TransactionSourceChoices
from apps.payments.models import Transaction
from apps.telegram_bot import icons
from apps.telegram_bot.ui import back_button, button
from apps.users.models import TelegramUser


async def get_reply_markup_balance(user: TelegramUser) -> InlineKeyboardMarkup:
    """Сроки пополнения по два в ряд: пять строк подряд читаются как список цен."""
    buttons: list[list[InlineKeyboardButton]] = [
        [
            button('1 месяц', 'top_up_balance_one_month'),
            button('2 месяца +5%', 'top_up_balance_two_month'),
        ],
        [
            button('3 месяца +10%', 'top_up_balance_three_month'),
            button('Полгода +20%', 'top_up_balance_six_month'),
        ],
        [
            button('Год +30%', 'top_up_balance_year'),
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
