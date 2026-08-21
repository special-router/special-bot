import logging
from typing import Final

from django.conf import settings
from django.utils.timezone import now
from py3xui import Client, Inbound

from apps.servers.internal_membership import sync_internal_memberships
from apps.servers.models import Server
from apps.vpn.models import UserVPN
from utils.py3xui.async_api import AsyncApi


logger = logging.getLogger(__name__)

INBOUND_ID: Final[int] = 1


def vpn_client_for(server: Server):
    """Панель, к которой обращается бот прямо сейчас.

    Единственное место, где решается 3x-ui или Remnawave. Обе реализации держат
    один контракт, поэтому откат миграции — это ``REMNAWAVE_ENABLED=false`` и
    пересоздание контейнера, без возврата кода.
    """
    if getattr(settings, 'REMNAWAVE_ENABLED', False):
        from apps.servers.remnawave_client import RemnawaveVPNClient

        return RemnawaveVPNClient(server)
    return APIVPNClient(server)


def _client_endpoint(client_vpn_host: str, inbound_port: int) -> tuple[str, int]:
    host, separator, configured_port = client_vpn_host.rpartition(':')
    if separator and configured_port.isdigit():
        return host, int(configured_port)
    return client_vpn_host, inbound_port


class APIVPNClient:
    def __init__(self, server: Server):
        self._server = server
        self._api: AsyncApi = AsyncApi(server.vpn_url, server.vpn_username, server.vpn_password)

    def _mirror_inbound_ids(self) -> list[int]:
        ids = getattr(settings, 'MIRROR_INBOUND_IDS', []) or []
        return [int(i) for i in ids if int(i) != self._server.inbound_id]

    async def add_user(self, user_vpn: UserVPN):
        await self._api.login()
        new_client = Client(
            id=str(user_vpn.vpn_uuid),
            email="",
            enable=True,
            limit_ip=settings.LIMIT_IP,
        )
        await self._api.client.add(self._server.inbound_id, [new_client])
        # Never create a canary membership: only an already retained exact UUID
        # can be enabled, and malformed/missing targets fail closed.
        await sync_internal_memberships(self._api, user_vpn, enabled=True)
        for inbound_id in self._mirror_inbound_ids():
            try:
                await self._api.client.add(inbound_id, [new_client])
            except Exception as error:
                # Mirror add is best-effort; the primary inbound is authoritative.
                # A misconfigured mirror id must stay visible instead of silent.
                logger.warning('Mirror add failed: inbound=%s reason=%s', inbound_id, type(error).__name__)

    async def remove_user(self, user_vpn: UserVPN):
        await self._api.login()
        # Retained canary identities are disabled rather than deleted so a
        # later reactivation cannot infer or recreate target ownership.
        await sync_internal_memberships(self._api, user_vpn, enabled=False)
        await self._api.inbound.delete_client_by_uuid(self._server.inbound_id, user_vpn.vpn_uuid)
        for inbound_id in self._mirror_inbound_ids():
            try:
                await self._api.inbound.delete_client_by_uuid(inbound_id, user_vpn.vpn_uuid)
            except Exception as error:
                logger.warning('Mirror delete failed: inbound=%s reason=%s', inbound_id, type(error).__name__)

    async def enable_user(self, user_vpn: UserVPN, enabled: bool = True):
        await self._api.login()
        await self._sync_enable(self._server.inbound_id, user_vpn, enabled, add_if_missing=True)
        await sync_internal_memberships(self._api, user_vpn, enabled=enabled)
        for inbound_id in self._mirror_inbound_ids():
            try:
                await self._sync_enable(inbound_id, user_vpn, enabled, add_if_missing=False)
            except Exception as error:
                logger.warning('Mirror enable sync failed: inbound=%s reason=%s', inbound_id, type(error).__name__)

    async def _sync_enable(self, inbound_id: int, user_vpn: UserVPN, enabled: bool, *, add_if_missing: bool):
        inbound = await self._api.inbound.get_by_id(inbound_id)
        client = next(
            (item for item in inbound.settings.clients if str(item.id) == str(user_vpn.vpn_uuid)),
            None,
        )
        if client is None:
            if not enabled or not add_if_missing:
                return
            new_client = Client(
                id=str(user_vpn.vpn_uuid),
                email="",
                enable=True,
                limit_ip=settings.LIMIT_IP,
            )
            await self._api.client.add(inbound_id, [new_client])
            return
        client.enable = enabled
        # py3x-ui selects an update route from this field; it also scopes the
        # attribution label the transport stamps on the way out.
        client.inbound_id = inbound_id
        await self._api.client.update(str(user_vpn.vpn_uuid), client)

    async def get_key(self, user_vpn: UserVPN):
        await self._api.login()

        inbound: Inbound = await self._api.inbound.get_by_id(self._server.inbound_id)

        public_key = inbound.stream_settings.reality_settings.get('settings').get('publicKey')
        website_name = inbound.stream_settings.reality_settings.get('serverNames')[0]
        short_id = inbound.stream_settings.reality_settings.get('shortIds')[0]
        client_host, port = _client_endpoint(user_vpn.server.client_vpn_host, inbound.port)

        connection_string = (
            f"vless://{user_vpn.vpn_uuid}@{client_host}:{port}"
            f"?type=tcp&security=reality&pbk={public_key}&fp=chrome&sni={website_name}"
            f"&sid={short_id}&spx=%2F#{user_vpn.user.telegram_id}"
        )

        return connection_string

    async def get_raw_inbound_config(self, user_vpn: UserVPN) -> dict:
        await self._api.login()

        inbound_json = await self._api.inbound.get_raw_config_by_id(self._server.inbound_id)
        # Prepare values
        protocol = inbound_json.get('protocol')
        port = inbound_json.get('port')
        address = self._server.client_vpn_host.split(':')[0]

        stream_settings = inbound_json.get('streamSettings', {})
        reality_settings = stream_settings.get('realitySettings', {})
        settings_inner = reality_settings.get('settings', {})

        server_names = reality_settings.get('serverNames') or []
        short_ids = reality_settings.get('shortIds') or []

        server_name = server_names[0] if server_names else ''
        short_id = short_ids[0] if short_ids else ''
        public_key = settings_inner.get('publicKey', '')
        fingerprint = settings_inner.get('fingerprint', 'chrome')
        spider_x = settings_inner.get('spiderX', '/')

        # Build outbound config
        outbound_config = {
            'outbounds': [
                {
                    'tag': 'proxy',
                    'protocol': protocol,
                    'settings': {
                        'vnext': [
                            {
                                'address': address,
                                'port': port,
                                'users': [
                                    {
                                        'id': str(user_vpn.vpn_uuid),
                                        'encryption': 'none',
                                        'flow': 'xtls-rprx-vision',
                                    }
                                ],
                            }
                        ]
                    },
                    'streamSettings': {
                        'sockopt': {'mark': 255},
                        'network': 'tcp',
                        'security': 'reality',
                        'realitySettings': {
                            'serverName': server_name,
                            'fingerprint': fingerprint,
                            'publicKey': public_key,
                            'shortId': short_id,
                            'spiderX': spider_x,
                        },
                    },
                }
            ]
        }

        return outbound_config