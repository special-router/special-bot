import uuid
from typing import Final

from apps.servers.models import Server
from py3xui import AsyncApi, Client, Inbound

from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN

INBOUND_ID: Final[int] = 1

class APIVPNClient:
    def __init__(self, server: Server):
        self._server = server
        self._api: AsyncApi = AsyncApi(server.vpn_url, server.vpn_username, server.vpn_password)

    async def add_user(self, user_vpn: UserVPN):
        await self._api.login()

        new_client = Client(id=str(user_vpn.vpn_uuid), email=str(user_vpn.user.telegram_id), enable=True)
        await self._api.client.add(self._server.inbound_id, [new_client])

    async def get_key(self, user_vpn: UserVPN):
        await self._api.login()

        inbound: Inbound = await self._api.inbound.get_by_id(self._server.inbound_id)

        public_key = inbound.stream_settings.reality_settings.get("settings").get("publicKey")
        website_name = inbound.stream_settings.reality_settings.get("serverNames")[0]
        short_id = inbound.stream_settings.reality_settings.get("shortIds")[0]

        connection_string = (
            f"vless://{user_vpn.vpn_uuid}@{user_vpn.server.client_vpn_host}"
            f"?type=tcp&security=reality&pbk={public_key}&fp=chrome&sni={website_name}"
            f"&sid={short_id}&spx=%2F#{user_vpn.user.telegram_id}"
        )

        return connection_string


