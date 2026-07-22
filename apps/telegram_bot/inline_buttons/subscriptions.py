from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from apps.payments.choices import ProductLineChoices
from apps.users.models import TelegramUser


async def get_reply_markup_subscriptions_menu() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text='💵 Баланс подписки', callback_data='sub_show_balance')],
        [InlineKeyboardButton(text='📅 Купить 1 месяц', callback_data='sub_buy:1')],
        [InlineKeyboardButton(text='📅 Купить 2 месяца', callback_data='sub_buy:2')],
        [InlineKeyboardButton(text='📅 Купить 3 месяца', callback_data='sub_buy:3')],
        [InlineKeyboardButton(text='📅 Купить 6 месяцев', callback_data='sub_buy:6')],
        [InlineKeyboardButton(text='📅 Купить 12 месяцев', callback_data='sub_buy:12')],
        [InlineKeyboardButton(text='🔗 Моя подписка', callback_data='sub_show_vpn')],
        [InlineKeyboardButton(text='Назад', callback_data='main_menu')],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def get_reply_markup_sub_balance(user: TelegramUser) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text='Пополнить на 1 месяц', callback_data='sub_top_up:30:0')],
        [InlineKeyboardButton(text='Пополнить на 2 месяца (+5%)', callback_data='sub_top_up:60:5')],
        [InlineKeyboardButton(text='Пополнить на 3 месяца (+10%)', callback_data='sub_top_up:90:10')],
        [InlineKeyboardButton(text='Пополнить на 6 месяцев (+20%)', callback_data='sub_top_up:180:20')],
        [InlineKeyboardButton(text='Пополнить на год (+30%)', callback_data='sub_top_up:365:30')],
        [InlineKeyboardButton(text='Назад', callback_data='subscriptions_menu')],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_reply_markup_payment_method(product_line: str, count_days: int, percent: int) -> InlineKeyboardMarkup:
    prefix = 'vpn' if product_line == ProductLineChoices.VPN_KEYS else 'sub'
    buttons = [
        [
            InlineKeyboardButton(
                text='💳 Карта / ЮMoney (Telegram)',
                callback_data=f'{prefix}_pay_tg:{count_days}:{percent}',
            )
        ],
        [
            InlineKeyboardButton(
                text='🪙 CryptoBot',
                callback_data=f'{prefix}_pay_crypto:{count_days}:{percent}',
            )
        ],
        [
            InlineKeyboardButton(
                text='Назад',
                callback_data='show_balance' if product_line == ProductLineChoices.VPN_KEYS else 'sub_show_balance',
            )
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)
