import json
import logging
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from py3xui import Client
from py3xui.async_api import AsyncClientApi

from utils.py3xui.async_api_inbound import AsyncInboundApi


PANEL = 'https://panel.invalid/'
CLIENT_UUID = '3f2b7a54-0c81-4d2e-9a77-6c1f0b5a9d34'


class PanelEndpointContractTests(IsolatedAsyncioTestCase):
    """Assert the URL each client operation puts on the wire, not that a mock was called.

    The pin guard in ops/scripts/validate_repository.py compares version strings;
    it cannot see behaviour, so it says nothing about a deliberate upgrade of the
    pin. These tests do: py3xui 0.7.0 routes update() to
    panel/api/clients/update/{email or uuid} and adds a separate del route, so
    swapping the library for one that addresses clients differently fails here.
    Every other test in this repository mocks api.client.update itself, which is
    why the 0.5.1/0.7.0 divergence went unnoticed.
    """

    @staticmethod
    def client_api():
        api = object.__new__(AsyncClientApi)
        api._post = AsyncMock()
        api._url = lambda endpoint: f'{PANEL}{endpoint}'
        api.logger = logging.getLogger(__name__)
        return api

    @staticmethod
    def endpoint(api):
        return api._post.await_args.args[0].removeprefix(PANEL)

    @staticmethod
    def client(email=''):
        return Client(id=CLIENT_UUID, email=email, enable=True, inbound_id=5)

    async def test_add_posts_to_the_collection_route_with_the_client_in_the_body(self):
        api = self.client_api()

        await api.add(5, [self.client()])

        self.assertEqual(self.endpoint(api), 'panel/api/inbounds/addClient')
        payload = api._post.await_args.args[2]
        self.assertEqual(payload['id'], 5)
        self.assertEqual(json.loads(payload['settings'])['clients'][0]['id'], CLIENT_UUID)

    async def test_update_addresses_the_client_by_uuid(self):
        api = self.client_api()

        await api.update(CLIENT_UUID, self.client())

        self.assertEqual(self.endpoint(api), f'panel/api/inbounds/updateClient/{CLIENT_UUID}')

    async def test_update_ignores_a_non_empty_email(self):
        # 0.5.1 has no email fallback; 0.7.0 routes to panel/api/clients/update/{email}
        # whenever the client carries one, and clients are about to get real emails.
        api = self.client_api()

        await api.update(CLIENT_UUID, self.client(email='user-801@special.invalid'))

        self.assertEqual(self.endpoint(api), f'panel/api/inbounds/updateClient/{CLIENT_UUID}')


class ScopedDeleteEndpointContractTests(IsolatedAsyncioTestCase):
    """delete_client_by_uuid() must stay inbound-scoped and UUID-addressed."""

    async def test_delete_addresses_the_uuid_inside_its_inbound(self):
        api = object.__new__(AsyncInboundApi)
        api.get_by_id = AsyncMock(side_effect=[self.inbound(CLIENT_UUID), self.inbound()])
        api._post = AsyncMock()
        api._url = lambda endpoint: f'{PANEL}{endpoint}'

        await api.delete_client_by_uuid(13, CLIENT_UUID)

        self.assertEqual(
            api._post.await_args.args[0].removeprefix(PANEL),
            f'panel/api/inbounds/13/delClient/{CLIENT_UUID}',
        )

    @staticmethod
    def inbound(*ids):
        return SimpleNamespace(settings=SimpleNamespace(
            clients=[SimpleNamespace(id=value) for value in ids],
        ))
