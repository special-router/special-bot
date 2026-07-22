from telegram import Update
from telegram.ext import ContextTypes

from apps.subscriptions.models import RouterDevice
from apps.telegram_bot.handlers.router.keyboards import (
    currency_keyboard,
    device_list_keyboard,
    manage_no_device_keyboard,
    tariff_payment_keyboard,
    tariffs_keyboard,
)
from apps.telegram_bot.utils import get_user


async def router_manage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await get_user(update)

    devices = [d async for d in RouterDevice.objects.filter(owner=user).order_by('-activated_at')]

    if not devices:
        await context.bot.send_message(
            user.telegram_id,
            text='У вас нет активного устройства SPECIAL Mini.\n\nАктивируйте роутер по серийному номеру.',
            reply_markup=manage_no_device_keyboard(),
        )
        return

    if len(devices) == 1:
        await _show_device_status(update, context, devices[0])
        return

    await context.bot.send_message(
        user.telegram_id,
        text='Выберите устройство для активации/продления подписки:',
        reply_markup=await device_list_keyboard(devices),
    )


async def router_select_device(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await get_user(update)
    device_id = int(update.callback_query.data.split(':')[1])
    device = await RouterDevice.objects.aget(id=device_id, owner=user)
    await _show_device_status(update, context, device)


async def _show_device_status(update: Update, context: ContextTypes.DEFAULT_TYPE, device: RouterDevice) -> None:
    user = await get_user(update)

    if device.is_subscription_active:
        valid = device.valid_until.strftime('%d.%m.%Y %H:%M')
        text = (
            f'📡 **{device.display_id}**\n\n'
            f'Статус: ✅ Активна\n'
            f'Действует до: **{valid}**'
        )
    else:
        valid = device.valid_until.strftime('%d.%m.%Y %H:%M') if device.valid_until else '—'
        text = (
            f'📡 **{device.display_id}**\n\n'
            f'Статус: ❌ Неактивна\n'
            f'Истекла: {valid}\n\n'
            f'Продлите подписку, чтобы восстановить доступ.'
        )

    await context.bot.send_message(
        user.telegram_id,
        text=text,
        parse_mode='Markdown',
        reply_markup=currency_keyboard(device.id),
    )


async def router_select_currency(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await get_user(update)
    parts = update.callback_query.data.split(':')
    device_id, currency = int(parts[1]), parts[2]
    device = await RouterDevice.objects.aget(id=device_id, owner=user)

    await context.bot.send_message(
        user.telegram_id,
        text=f'Тарифы для **{device.display_id}** ({currency}):',
        parse_mode='Markdown',
        reply_markup=tariffs_keyboard(device_id, currency),
    )


async def router_select_tariff(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await get_user(update)
    parts = update.callback_query.data.split(':')
    device_id, currency, months = int(parts[1]), parts[2], int(parts[3])
    device = await RouterDevice.objects.aget(id=device_id, owner=user)

    from apps.subscriptions.constants import ROUTER_TARIFFS_RUB, ROUTER_TARIFFS_USDT

    price = ROUTER_TARIFFS_RUB[months] if currency == 'RUB' else ROUTER_TARIFFS_USDT[months]
    suffix = '₽' if currency == 'RUB' else 'USDT'

    await context.bot.send_message(
        user.telegram_id,
        text=f'**{device.display_id}** — {months} мес — **{price} {suffix}**\n\nВыберите способ оплаты:',
        parse_mode='Markdown',
        reply_markup=tariff_payment_keyboard(device_id, currency, months),
    )


async def router_subscription_confirmed(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    device: RouterDevice,
    user=None,
) -> None:
    if user is None:
        user = await get_user(update)
    valid = device.valid_until.strftime('%d.%m.%Y') if device.valid_until else '—'
    await context.bot.send_message(
        user.telegram_id,
        text=f'✅ Подписка для устройства **{device.display_id}** активна до **{valid}**.',
        parse_mode='Markdown',
    )
