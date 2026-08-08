"""Feature-gated connector for 3x-ui's built-in subscription service.

This module deliberately is not called by legacy key issuance.  It may only
assign a subscription ID to a client that already exists in the configured
inbound when SUBSCRIPTION_CONNECTOR_ENABLED is explicitly enabled.
"""

from dataclasses import dataclass
from secrets import token_hex
from urllib.parse import urlsplit

from django.conf import settings

from apps.servers.models import Server
from apps.vpn.models import UserVPN
from utils.py3xui.async_api import AsyncApi


class SubscriptionConnectorDisabled(RuntimeError):
    """Raised before any control-plane mutation while the rollout is inactive."""


class SubscriptionClientMissing(RuntimeError):
    """Raised when a database VPN record has no matching 3x-ui client."""


@dataclass(frozen=True)
class SubscriptionReference:
    sub_id: str
    url: str


def build_subscription_url(base_url: str, sub_id: str) -> str:
    """Build an HTTPS subscription URL without accepting malformed endpoints."""
    parsed = urlsplit(base_url)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError('SUBSCRIPTION_BASE_URL must be an HTTPS origin or path without query or fragment.')
    if not sub_id:
        raise ValueError('3x-ui client subId must not be empty.')
    return f'{base_url.rstrip("/")}/{sub_id}'


class XUISubscriptionConnector:
    """Manage 3x-ui client ``subId`` values behind an explicit rollout gate."""

    def __init__(self, server: Server):
        self._server = server
        self._api = AsyncApi(server.vpn_url, server.vpn_username, server.vpn_password)

    @staticmethod
    def is_enabled() -> bool:
        return settings.SUBSCRIPTION_CONNECTOR_ENABLED

    @staticmethod
    def _reference(sub_id: str) -> SubscriptionReference:
        return SubscriptionReference(
            sub_id=sub_id,
            url=build_subscription_url(settings.SUBSCRIPTION_BASE_URL, sub_id),
        )

    async def _get_client(self, user_vpn: UserVPN):
        await self._api.login()
        inbound = await self._api.inbound.get_by_id(self._server.inbound_id)
        client = next(
            (item for item in inbound.settings.clients if str(item.id) == str(user_vpn.vpn_uuid)),
            None,
        )
        if client is None:
            raise SubscriptionClientMissing('The VPN client is absent from the configured 3x-ui inbound.')
        return client

    async def get_existing_subscription_reference(self, user_vpn: UserVPN) -> SubscriptionReference:
        """Read an existing subId; never mutate 3x-ui."""
        if not self.is_enabled():
            raise SubscriptionConnectorDisabled('3x-ui subscription connector is disabled.')
        client = await self._get_client(user_vpn)
        if not client.sub_id:
            raise SubscriptionClientMissing('The 3x-ui client has no subscription ID.')
        return self._reference(client.sub_id)

    async def ensure_subscription_reference(self, user_vpn: UserVPN) -> SubscriptionReference:
        """Assign a subId to an existing 3x-ui client, only after explicit activation.

        This intentionally never creates, enables, disables, deletes, or changes
        expiry of a client.  Those billing and migration semantics are separate
        rollout steps.
        """
        if not self.is_enabled():
            raise SubscriptionConnectorDisabled('3x-ui subscription connector is disabled.')

        client = await self._get_client(user_vpn)

        sub_id = client.sub_id or token_hex(16)
        if not client.sub_id:
            client.sub_id = sub_id
            client.inbound_id = self._server.inbound_id
            await self._api.client.update(str(user_vpn.vpn_uuid), client)

        return self._reference(sub_id)
