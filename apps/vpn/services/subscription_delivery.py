"""Subscription delivery helpers kept separate from legacy VLESS issuance."""

import logging

from django.conf import settings

from apps.servers.subscription_connector import (
    SubscriptionClientMissing,
    SubscriptionConnectorDisabled,
    XUISubscriptionConnector,
)
from apps.vpn.models import UserVPN


logger = logging.getLogger(__name__)


async def get_subscription_url(user_vpn: UserVPN) -> str:
    """Return an existing 3x-ui subscription URL without changing control plane.

    Display flows use this read-only path for disabled connections so viewing
    a profile can never create a new subscription identity.
    """
    connector = XUISubscriptionConnector(user_vpn.server)
    reference = await connector.get_existing_subscription_reference(user_vpn)
    return reference.url


async def prepare_subscription_url(user_vpn: UserVPN) -> str:
    """Assign a missing subId and return its URL when the connector is enabled."""
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
