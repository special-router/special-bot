import json
from urllib.parse import quote

from py3xui import Inbound
from py3xui.api.api_base import ApiFields
from py3xui.async_api import AsyncInboundApi as BaseAsyncInboundApi


class AsyncInboundApi(BaseAsyncInboundApi):
    async def delete_client_by_uuid(self, inbound_id: int, client_uuid: str) -> None:
        """Delete exactly one UUID from one inbound, never resolving empty email.

        py3xui's generic client.delete() resolves a client through its email
        first. SPECIAL clients deliberately have no email, so deletion must be
        constrained by the selected inbound and verified before and after the
        mutation.
        """
        inbound = await self.get_by_id(inbound_id)
        matches = [
            client for client in (inbound.settings.clients or [])
            if str(client.id) == str(client_uuid)
        ]
        if len(matches) != 1:
            raise RuntimeError('xui_scoped_delete_ownership')

        endpoint = f'panel/api/inbounds/{int(inbound_id)}/delClient/{quote(str(client_uuid), safe="")}'
        await self._post(self._url(endpoint), {'Accept': 'application/json'}, {})

        refreshed = await self.get_by_id(inbound_id)
        remaining = [
            client for client in (refreshed.settings.clients or [])
            if str(client.id) == str(client_uuid)
        ]
        if remaining:
            raise RuntimeError('xui_scoped_delete_verification')

    async def get_raw_config_by_id(self, inbound_id: int) -> Inbound:
        endpoint = f"panel/api/inbounds/get/{inbound_id}"
        headers = {'Accept': 'application/json'}

        url = self._url(endpoint)
        self.logger.info('Getting inbound by ID: %s', inbound_id)

        response = await self._get(url, headers)

        inbound_json = response.json().get(ApiFields.OBJ)
        inbound_json['settings'] = json.loads(inbound_json['settings'])
        inbound_json['streamSettings'] = json.loads(inbound_json['streamSettings'])
        inbound_json['sniffing'] = json.loads(inbound_json['sniffing'])
        return inbound_json
