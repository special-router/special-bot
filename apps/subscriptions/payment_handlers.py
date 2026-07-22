import json

from apps.subscriptions.models import RouterDevice, RouterOrder
from apps.subscriptions.router_services import complete_activation_payment, extend_device_subscription
from apps.telegram_bot.handlers.router.buy import router_buy_paid_shipping_prompt
from apps.telegram_bot.handlers.router.manage import router_subscription_confirmed


async def handle_router_payment_payload(payload: dict, user, update, context) -> bool:
    if payload.get('type') != 'router':
        return False

    order_type = payload.get('order_type')
    device_id = payload.get('device_id')
    months = payload.get('months', 1)

    if order_type == 'ROUTER_PURCHASE':
        order = await RouterOrder.objects.filter(
            user=user,
            order_type=RouterOrder.OrderType.ROUTER_PURCHASE,
            status=RouterOrder.Status.PENDING,
        ).order_by('-created_at').afirst()
        if order:
            order.status = RouterOrder.Status.PAID
            await order.asave(update_fields=['status'])
        await router_buy_paid_shipping_prompt(update, context, user=user)
        return True

    if not device_id:
        return False

    device = await RouterDevice.objects.select_related('owner').aget(id=device_id)
    if device.owner_id and device.owner_id != user.id:
        return True

    if order_type == 'ACTIVATION':
        if not device.owner_id:
            from apps.subscriptions.router_services import activate_device_for_user

            device = await activate_device_for_user(device, user)
        device = await complete_activation_payment(device, user)
        await RouterOrder.objects.acreate(
            user=user,
            order_type=RouterOrder.OrderType.ACTIVATION,
            status=RouterOrder.Status.PAID,
            amount=500,
            currency='RUB',
            device=device,
            months=1,
        )
    elif order_type == 'SUBSCRIPTION':
        device = await extend_device_subscription(device, months)
        from apps.subscriptions.constants import ROUTER_TARIFFS_RUB, ROUTER_TARIFFS_USDT

        currency = payload.get('currency', 'RUB')
        amount = ROUTER_TARIFFS_RUB.get(months, 0) if currency == 'RUB' else ROUTER_TARIFFS_USDT.get(months, 0)
        await RouterOrder.objects.acreate(
            user=user,
            order_type=RouterOrder.OrderType.SUBSCRIPTION,
            status=RouterOrder.Status.PAID,
            amount=amount,
            currency=currency,
            device=device,
            months=months,
        )

    await router_subscription_confirmed(update, context, device, user=user)
    return True


def parse_payment_payload(raw: str) -> dict:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
