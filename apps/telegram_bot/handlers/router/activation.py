from telegram import Update
from telegram.ext import ContextTypes

from apps.subscriptions.constants import SUPPORT_USERNAME
from apps.subscriptions.models import RouterDevice
from apps.subscriptions.router_services import activate_device_for_user, find_device_by_serial
from apps.telegram_bot.handlers.router.keyboards import (
    activation_intro_keyboard,
    activation_pay_keyboard,
    activation_payment_method_keyboard,
    activation_retry_keyboard,
)
from apps.telegram_bot.handlers.router.states import ROUTER_STATE_SERIAL, set_router_state
from apps.telegram_bot.utils import get_user


ACTIVATION_INTRO = """
🔐 **Активация устройства**

На стикере устройства SPECIAL Mini указан серийный номер.
Введите его для привязки роутера к вашему аккаунту.
"""


async def router_activation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await get_user(update)
    await context.bot.send_message(
        user.telegram_id,
        text=ACTIVATION_INTRO,
        parse_mode='Markdown',
        reply_markup=activation_intro_keyboard(),
    )


async def router_enter_serial(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await get_user(update)
    set_router_state(context, ROUTER_STATE_SERIAL)
    await context.bot.send_message(
        user.telegram_id,
        text='Введите серийный номер с наклейки устройства (например: SPM00001):',
    )


async def process_serial_number(update: Update, context: ContextTypes.DEFAULT_TYPE, serial: str) -> None:
    user = await get_user(update)
    device = await find_device_by_serial(serial)

    if device is None:
        await update.message.reply_text(
            '❌ Серийный номер не найден.',
            reply_markup=activation_retry_keyboard(),
        )
        return

    if device.owner_id and device.owner_id != user.id:
        await update.message.reply_text(
            f'❌ Устройство {device.display_id} уже активировано.\n\nПоддержка: {SUPPORT_USERNAME}',
            reply_markup=activation_retry_keyboard(),
        )
        return

    if device.owner_id == user.id:
        if device.is_subscription_active:
            valid = device.valid_until.strftime('%d.%m.%Y') if device.valid_until else '—'
            await update.message.reply_text(
                f'✅ Устройство {device.display_id} уже активировано.\nПодписка активна до {valid}.',
            )
            return
        await update.message.reply_text(
            f'Устройство **{device.display_id}** привязано.\nОплатите первый месяц — **500 ₽**.',
            parse_mode='Markdown',
            reply_markup=activation_pay_keyboard(device.id),
        )
        return

    device = await activate_device_for_user(device, user)

    await update.message.reply_text(
        f'✅ Устройство **{device.display_id}** активировано!\n\n'
        f'Оплатите первый месяц подписки — **500 ₽**.',
        parse_mode='Markdown',
        reply_markup=activation_pay_keyboard(device.id),
    )


async def router_activation_pay_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await get_user(update)
    device_id = int(update.callback_query.data.split(':')[1])
    device = await RouterDevice.objects.aget(id=device_id, owner=user)

    await context.bot.send_message(
        user.telegram_id,
        text=f'Оплата подписки для **{device.display_id}**\n\nВыберите способ оплаты:',
        parse_mode='Markdown',
        reply_markup=activation_payment_method_keyboard(device_id),
    )
