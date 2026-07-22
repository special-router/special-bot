from telegram import Update
from telegram.ext import ContextTypes

from apps.subscriptions.models import RouterOrder
from apps.telegram_bot.handlers.router.activation import process_serial_number
from apps.telegram_bot.handlers.router.buy import router_buy_paid_shipping_prompt
from apps.telegram_bot.utils import get_user

from apps.telegram_bot.handlers.router.states import (
    ROUTER_STATE_SERIAL,
    ROUTER_STATE_SHIPPING,
    set_router_state,
    get_router_state,
)


async def router_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = get_router_state(context)
    if not state or not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    set_router_state(context, None)

    if state == ROUTER_STATE_SERIAL:
        await process_serial_number(update, context, text)
        return

    if state == ROUTER_STATE_SHIPPING:
        user = await get_user(update)
        order = await RouterOrder.objects.filter(
            user=user,
            order_type=RouterOrder.OrderType.ROUTER_PURCHASE,
            status=RouterOrder.Status.PAID,
        ).order_by('-created_at').afirst()

        if order:
            order.shipping_data = {'text': text}
            order.status = RouterOrder.Status.SHIPPING
            await order.asave(update_fields=['shipping_data', 'status'])

        await update.message.reply_text(
            '✅ Данные для доставки сохранены.\n'
            'Менеджер @Special_Wifi_Official проверит заказ и пришлёт трек-номер в бот.'
        )
