from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from apps.subscriptions.constants import SUPPORT_URL
from apps.subscriptions.models import RouterDevice


def back_main() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text='Назад', callback_data='main_menu')]


def consent_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='Согласен/а', callback_data='router_buy_consent')],
            back_main(),
        ]
    )


def router_purchase_payment_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='Оплатить (RUB) 12 000 RUB', callback_data='router_buy_pay:RUB')],
            [InlineKeyboardButton(text='Оплатить (USDT) 120 USDT', callback_data='router_buy_pay:USDT')],
            [InlineKeyboardButton(text='Назад', callback_data='router_buy')],
        ]
    )


def activation_intro_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='Ввести серийный номер', callback_data='router_enter_serial')],
            back_main(),
        ]
    )


def activation_retry_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='Ввести снова', callback_data='router_enter_serial')],
            [InlineKeyboardButton(text='Поддержка', url=SUPPORT_URL)],
            back_main(),
        ]
    )


def activation_pay_keyboard(device_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='Оплатить подписку', callback_data=f'router_activation_pay:{device_id}')],
            back_main(),
        ]
    )


def activation_payment_method_keyboard(device_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text='ЮKassa (RUB) 500 ₽',
                    callback_data=f'router_act_pay_tg:{device_id}',
                )
            ],
            [
                InlineKeyboardButton(
                    text='CryptoBot',
                    callback_data=f'router_act_pay_crypto:{device_id}',
                )
            ],
            [InlineKeyboardButton(text='Назад', callback_data='router_activation')],
        ]
    )


def manage_no_device_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='Активация устройства', callback_data='router_activation')],
            back_main(),
        ]
    )


async def device_list_keyboard(devices: list[RouterDevice]) -> InlineKeyboardMarkup:
    buttons = []
    for device in devices:
        status = 'активна' if device.is_subscription_active else 'истекла'
        label = f'{device.display_id} — до {device.valid_until.strftime("%d.%m.%Y") if device.valid_until else "—"} ({status})'
        buttons.append(
            [InlineKeyboardButton(text=label, callback_data=f'router_device:{device.id}')]
        )
    buttons.append(back_main())
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def currency_keyboard(device_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='Оплатить (RUB)', callback_data=f'router_currency:{device_id}:RUB')],
            [InlineKeyboardButton(text='Оплатить (USDT)', callback_data=f'router_currency:{device_id}:USDT')],
            [InlineKeyboardButton(text='Назад', callback_data='router_manage')],
        ]
    )


def tariffs_keyboard(device_id: int, currency: str) -> InlineKeyboardMarkup:
    from apps.subscriptions.constants import ROUTER_TARIFFS_RUB, ROUTER_TARIFFS_USDT

    tariffs = ROUTER_TARIFFS_RUB if currency == 'RUB' else ROUTER_TARIFFS_USDT
    suffix = '₽' if currency == 'RUB' else 'USDT $'

    buttons = []
    for months, price in tariffs.items():
        label = f'{months} мес — {price} {suffix}'
        buttons.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f'router_tariff:{device_id}:{currency}:{months}',
                )
            ]
        )
    buttons.append([InlineKeyboardButton(text='Назад', callback_data=f'router_device:{device_id}')])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def tariff_payment_keyboard(device_id: int, currency: str, months: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text='ЮKassa (RUB)' if currency == 'RUB' else 'ЮKassa',
                    callback_data=f'router_sub_pay_tg:{device_id}:{currency}:{months}',
                )
            ],
            [
                InlineKeyboardButton(
                    text='CryptoBot',
                    callback_data=f'router_sub_pay_crypto:{device_id}:{currency}:{months}',
                )
            ],
            [InlineKeyboardButton(text='Назад', callback_data=f'router_currency:{device_id}:{currency}')],
        ]
    )


def special_router_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='Активация устройства', callback_data='router_activation')],
            [InlineKeyboardButton(text='Управление подпиской', callback_data='router_manage')],
            [InlineKeyboardButton(text='Купить роутер Special Mini', callback_data='router_buy')],
            back_main(),
        ]
    )
