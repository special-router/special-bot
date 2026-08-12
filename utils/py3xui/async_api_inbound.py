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

    async def set_enabled(self, inbound_id: int, enabled: bool) -> bool:
        """Flip only an inbound's enable flag, preserving its stored config.

        py3xui's update() re-serializes the whole model, which can silently
        normalize protocol settings. Retiring an inbound must not rewrite the
        configuration it may later be restored from, so the panel's own encoded
        strings are sent back untouched and the result is verified.
        """
        endpoint = f'panel/api/inbounds/get/{int(inbound_id)}'
        response = await self._get(self._url(endpoint), {'Accept': 'application/json'})
        stored = response.json().get(ApiFields.OBJ)
        if not stored:
            raise RuntimeError('xui_inbound_missing')
        if bool(stored.get('enable')) == enabled:
            return False

        payload = dict(stored)
        payload['enable'] = enabled
        await self._post(
            self._url(f'panel/api/inbounds/update/{int(inbound_id)}'),
            {'Accept': 'application/json'},
            payload,
        )

        verify = await self._get(self._url(endpoint), {'Accept': 'application/json'})
        if bool(verify.json().get(ApiFields.OBJ, {}).get('enable')) != enabled:
            raise RuntimeError('xui_inbound_enable_verification')
        return True

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
