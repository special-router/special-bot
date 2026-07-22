import re
from datetime import timedelta

from django.utils.timezone import now

from apps.payments.choices import ProductLineChoices
from apps.subscriptions.models import RouterDevice, RouterOrder, SubscriptionPlan
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN
from apps.vpn.services.add_vpn_to_user import add_vpn_to_user
from apps.subscriptions.services import get_subscription_server


def normalize_serial(serial: str) -> str:
    return re.sub(r'\s+', '', serial.strip().upper())


async def find_device_by_serial(serial: str) -> RouterDevice | None:
    normalized = normalize_serial(serial)
    return await RouterDevice.objects.filter(serial_number=normalized).afirst()


async def provision_vpn_for_device(device: RouterDevice, user: TelegramUser) -> UserVPN:
    plan = await SubscriptionPlan.objects.filter(is_active=True).select_related('server').afirst()
    server = await get_subscription_server(plan) if plan else None
    if server is None:
        from apps.servers.models import Server

        server = await Server.objects.with_related_tariffs().filter(is_subscription_server=True).afirst()
        if server is None:
            server = await Server.objects.with_related_tariffs().order_by('?').afirst()

    if device.user_vpn_id:
        user_vpn = await UserVPN.objects.with_related_server().aget(id=device.user_vpn_id)
        return user_vpn

    user_vpn = await add_vpn_to_user(user, server, product_line=ProductLineChoices.SUBSCRIPTION)
    device.user_vpn = user_vpn
    await device.asave(update_fields=['user_vpn'])
    return user_vpn


async def activate_device_for_user(device: RouterDevice, user: TelegramUser) -> RouterDevice:
    device.owner = user
    device.activated_at = now()
    await device.asave(update_fields=['owner', 'activated_at'])
    await provision_vpn_for_device(device, user)
    return device


async def extend_device_subscription(device: RouterDevice, months: int) -> RouterDevice:
    extension = timedelta(days=30 * months)
    base = device.valid_until if device.valid_until and device.valid_until > now() else now()
    device.valid_until = base + extension

    if device.owner_id and not device.user_vpn_id:
        await provision_vpn_for_device(device, device.owner)

    if device.user_vpn_id:
        user_vpn = await UserVPN.objects.aget(id=device.user_vpn_id)
        user_vpn.valid_until = device.valid_until
        user_vpn.enabled = True
        await user_vpn.asave(update_fields=['valid_until', 'enabled', 'updated_at'])

    await device.asave(update_fields=['valid_until'])
    return device


async def complete_activation_payment(device: RouterDevice, user: TelegramUser) -> RouterDevice:
    if not device.owner_id:
        device = await activate_device_for_user(device, user)
    device.valid_until = now() + timedelta(days=30)
    await device.asave(update_fields=['valid_until'])

    if device.user_vpn_id:
        user_vpn = await UserVPN.objects.aget(id=device.user_vpn_id)
        user_vpn.valid_until = device.valid_until
        user_vpn.enabled = True
        await user_vpn.asave(update_fields=['valid_until', 'enabled', 'updated_at'])
    elif device.owner_id:
        await provision_vpn_for_device(device, user)

    return device
