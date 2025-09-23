from typing import Final

from django.utils.timezone import now
from py3xui import Client, Inbound

from apps.servers.models import Server
from apps.vpn.models import UserVPN
from utils.py3xui.async_api import AsyncApi

INBOUND_ID: Final[int] = 1


class APIVPNClient:
    def __init__(self, server: Server):
        self._server = server
        self._api: AsyncApi = AsyncApi(server.vpn_url, server.vpn_username, server.vpn_password)

    async def add_user(self, user_vpn: UserVPN):
        await self._api.login()
        new_client = Client(
            id=str(user_vpn.vpn_uuid),
            email=f'{str(user_vpn.user.telegram_id)} - {now().isoformat()}',
            enable=True,
        )
        await self._api.client.add(self._server.inbound_id, [new_client])

    async def remove_user(self, user_vpn: UserVPN):
        await self._api.login()
        new_client = Client(id=str(user_vpn.vpn_uuid), email=str(user_vpn.user.telegram_id), enable=True)
        await self._api.client.delete(self._server.inbound_id, [new_client])

    async def enable_user(self, user_vpn: UserVPN, enabled: bool = True):
        await self._api.login()

        client = await self._api.client.get_by_email(str(user_vpn.user.telegram_id))
        client.enable = enabled
        client.id = str(user_vpn.vpn_uuid)
        await self._api.client.update(client.id, client)

        user_vpn.enabled = enabled
        await user_vpn.asave()

    async def get_key(self, user_vpn: UserVPN):
        await self._api.login()

        inbound: Inbound = await self._api.inbound.get_by_id(self._server.inbound_id)

        public_key = inbound.stream_settings.reality_settings.get('settings').get('publicKey')
        website_name = inbound.stream_settings.reality_settings.get('serverNames')[0]
        short_id = inbound.stream_settings.reality_settings.get('shortIds')[0]

        connection_string = (
            f"vless://{user_vpn.vpn_uuid}@{user_vpn.server.client_vpn_host}"
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

        server_name = server_names[0] if server_names else ""
        short_id = short_ids[0] if short_ids else ""
        public_key = settings_inner.get('publicKey', "")
        fingerprint = settings_inner.get('fingerprint', "chrome")
        spider_x = settings_inner.get('spiderX', "/")

        # Build outbound config
        outbound_config = {
            "outbounds": [
                {
                    "tag": "proxy",
                    "protocol": protocol,
                    "settings": {
                        "vnext": [
                            {
                                "address": address,
                                "port": port,
                                "users": [
                                    {
                                        "id": str(user_vpn.vpn_uuid),
                                        "encryption": "none",
                                        "flow": "xtls-rprx-vision",
                                    }
                                ],
                            }
                        ]
                    },
                    "streamSettings": {
                        "sockopt": {"mark": 255},
                        "network": "tcp",
                        "security": "reality",
                        "realitySettings": {
                            "serverName": server_name,
                            "fingerprint": fingerprint,
                            "publicKey": public_key,
                            "shortId": short_id,
                            "spiderX": spider_x,
                        },
                    },
                }
            ]
        }

        return outbound_config
