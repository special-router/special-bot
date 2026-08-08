"""Subscription delivery helpers kept separate from legacy VLESS issuance."""

from apps.servers.subscription_connector import (
    SubscriptionClientMissing,
    SubscriptionConnectorDisabled,
    XUISubscriptionConnector,
)
from apps.vpn.models import UserVPN


async def get_subscription_url(user_vpn: UserVPN) -> str:
    """Return an existing 3x-ui subscription URL without changing control plane.

    This is intentionally not called by Telegram handlers yet. A later rollout
    can use it after the canary and migration gates pass.
    """
    connector = XUISubscriptionConnector(user_vpn.server)
    reference = await connector.get_existing_subscription_reference(user_vpn)
    return reference.url


async def prepare_subscription_url(user_vpn: UserVPN) -> str:
    """Assign a missing subId and return its URL when the connector is enabled."""
    connector = XUISubscriptionConnector(user_vpn.server)
    reference = await connector.ensure_subscription_reference(user_vpn)
    return reference.url


__all__ = [
    'SubscriptionClientMissing',
    'SubscriptionConnectorDisabled',
    'get_subscription_url',
    'prepare_subscription_url',
]
