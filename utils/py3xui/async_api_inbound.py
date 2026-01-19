import json

from py3xui import Inbound
from py3xui.api.api_base import ApiFields
from py3xui.async_api import AsyncInboundApi as BaseAsyncInboundApi


class AsyncInboundApi(BaseAsyncInboundApi):
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
