from datetime import timedelta

from django.utils.timezone import now

from apps.payments.choices import ProductLineChoices, TransactionSourceChoices, TransactionStatusChoices
from apps.payments.models import Transaction
from apps.servers.models import Server
from apps.servers.vpn_client import APIVPNClient
from apps.subscriptions.models import SubscriptionPlan
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN
from apps.vpn.services.add_vpn_to_user import add_vpn_to_user


async def get_active_subscription_plan() -> SubscriptionPlan:
    plan = await SubscriptionPlan.objects.filter(is_active=True).select_related('server').afirst()
    if plan is None:
        raise SubscriptionPlan.DoesNotExist('Нет активного тарифа подписки')
    return plan


async def get_subscription_server(plan: SubscriptionPlan) -> Server:
    if plan.server_id:
        return plan.server

    server = await Server.objects.with_related_tariffs().filter(is_subscription_server=True).order_by('?').afirst()
    if server is None:
        server = await Server.objects.with_related_tariffs().order_by('?').afirst()
    if server is None:
        raise Server.DoesNotExist('Нет доступных серверов')
    return server


async def activate_subscription(user: TelegramUser, months: int) -> UserVPN:
    from django.conf import settings

    plan = await get_active_subscription_plan()
    total_price = plan.monthly_price * months

    user_with_balance = await TelegramUser.objects.annotate_balance(
        product_line=ProductLineChoices.SUBSCRIPTION
    ).aget(id=user.id)

    if user_with_balance.balance < total_price:
        raise ValueError('Недостаточно средств на балансе подписок')

    server = await get_subscription_server(plan)
    extension = timedelta(days=30 * months)

    existing = (
        await UserVPN.objects.filter_by_user(user_id=user.id)
        .filter_by_product_line(ProductLineChoices.SUBSCRIPTION)
        .filter_by_enabled(True)
        .afirst()
    )

    if not existing:
        active_count = await (
            UserVPN.objects.filter_by_user(user_id=user.id)
            .filter_by_product_line(ProductLineChoices.SUBSCRIPTION)
            .acount()
        )
        if active_count >= settings.MAX_SUBSCRIPTION_KEYS:
            raise ValueError('Достигнут лимит подписок на аккаунт')

    if existing:
        base_date = existing.valid_until if existing.valid_until and existing.valid_until > now() else now()
        existing.valid_until = base_date + extension
        await existing.asave(update_fields=['valid_until', 'updated_at'])

        vpn_client = APIVPNClient(server)
        await vpn_client.enable_user(existing, enabled=True)
        user_vpn = existing
    else:
        user_vpn = await add_vpn_to_user(
            user,
            server,
            product_line=ProductLineChoices.SUBSCRIPTION,
        )
        user_vpn.valid_until = now() + extension
        await user_vpn.asave(update_fields=['valid_until', 'updated_at'])

    await Transaction.objects.acreate(
        user=user,
        amount=-total_price,
        status=TransactionStatusChoices.SUCCESS,
        source=TransactionSourceChoices.BUY,
        product_line=ProductLineChoices.SUBSCRIPTION,
    )

    return user_vpn
