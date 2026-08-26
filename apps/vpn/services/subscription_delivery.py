"""Subscription delivery helpers kept separate from legacy VLESS issuance."""

import logging

from django.conf import settings

from apps.servers import remnawave_subscription
from apps.servers.subscription_connector import (
    SubscriptionClientMissing,
    SubscriptionConnectorDisabled,
    XUISubscriptionConnector,
)
from apps.vpn.models import UserVPN


logger = logging.getLogger(__name__)


def _remnawave_enabled() -> bool:
    return bool(getattr(settings, 'REMNAWAVE_ENABLED', False))


async def get_subscription_url(user_vpn: UserVPN) -> str:
    """Существующая ссылка подписки, без изменения control plane.

    Экраны просмотра ходят по этому пути и для отключённых записей, поэтому
    открытие профиля не может создать новую личность подписки.
    """
    if _remnawave_enabled():
        reference = await remnawave_subscription.subscription_reference(user_vpn)
        return reference.url
    connector = XUISubscriptionConnector(user_vpn.server)
    reference = await connector.get_existing_subscription_reference(user_vpn)
    return reference.url


async def prepare_subscription_url(user_vpn: UserVPN) -> str:
    """Ссылка подписки для выдачи клиенту."""
    if _remnawave_enabled():
        # shortUuid неизменяем после создания. Runtime-создание заранее
        # сохраняет тот же sub_id, а исторический drift чинит отдельная
        # reconciliation-команда; выдача остаётся полностью read-only.
        reference = await remnawave_subscription.subscription_reference(user_vpn)
        return reference.url
    connector = XUISubscriptionConnector(user_vpn.server)
    reference = await connector.ensure_subscription_reference(user_vpn)
    return reference.url


async def get_user_access_url(user_vpn: UserVPN) -> str:
    """Prefer a subscription URL while preserving the direct VLESS fallback."""
    if not settings.SUBSCRIPTION_DELIVERY_ENABLED:
        return user_vpn.vpn_key

    try:
        if user_vpn.enabled:
            return await prepare_subscription_url(user_vpn)
        return await get_subscription_url(user_vpn)
    except Exception as error:
        # Do not log the URL, client identity or control-plane exception text.
        logger.warning('Subscription delivery fallback: %s', type(error).__name__)
        return user_vpn.vpn_key


__all__ = [
    'SubscriptionClientMissing',
    'SubscriptionConnectorDisabled',
    'get_subscription_url',
    'get_user_access_url',
    'prepare_subscription_url',
]
