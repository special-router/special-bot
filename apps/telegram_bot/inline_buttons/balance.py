from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from apps.payments.choices import TransactionSourceChoices
from apps.payments.models import Transaction
from apps.users.models import TelegramUser


async def get_reply_markup_balance(user: TelegramUser) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text='Пополнить на один месяц', callback_data=f'top_up_balance_one_month'),
        ],
        [
            InlineKeyboardButton(text='Пополнить на два месяца', callback_data=f'top_up_balance_two_month'),
        ],
        [
            InlineKeyboardButton(text='Пополнить на три месяца', callback_data=f'top_up_balance_three_month'),
        ],
        [
            InlineKeyboardButton(text='Пополнить на полгода', callback_data=f'top_up_balance_six_month'),
        ],
        [
            InlineKeyboardButton(text='Пополнить на год', callback_data=f'top_up_balance_year'),
        ],
    ]

    if not (
            await Transaction.objects.filter_by_user(
                user_id=user.id,
            ).filter_by_source(
                source=TransactionSourceChoices.PROMO,
            ).aexists()
    ):
        buttons += [[
            InlineKeyboardButton(text='Бесплатно 7 дней', callback_data=f'top_up_balance_promo'),
        ]]

    return InlineKeyboardMarkup(inline_keyboard=buttons)