from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, patch

from apps.servers.vpn_client import APIVPNClient, _client_endpoint


class ClientEndpointTests(TestCase):
    def test_endpoint_uses_configured_host_port_without_duplication(self):
        self.assertEqual(_client_endpoint('201.34.132.118:443', 443), ('201.34.132.118', 443))

    def test_endpoint_uses_inbound_port_when_host_has_no_port(self):
        self.assertEqual(_client_endpoint('201.34.132.118', 443), ('201.34.132.118', 443))

    @patch('apps.servers.vpn_client.AsyncApi')
    def test_legacy_key_has_no_forced_vision_flow(self, api_class):
        client = APIVPNClient(
            SimpleNamespace(
                vpn_url='',
                vpn_username='',
                vpn_password='',
                client_vpn_host='201.34.132.118:443',
                inbound_id=5,
            )
        )
        client._api.login = AsyncMock()
        client._api.inbound.get_by_id = AsyncMock(
            return_value=SimpleNamespace(
                port=443,
                stream_settings=SimpleNamespace(
                    reality_settings={
                        'settings': {'publicKey': 'public-key'},
                        'serverNames': ['example.com'],
                        'shortIds': ['abc123'],
                    }
                ),
            )
        )
        user_vpn = SimpleNamespace(
            vpn_uuid='uuid',
            user=SimpleNamespace(telegram_id=1),
            server=client._server,
        )

        key = self.async_run(client.get_key(user_vpn))

        self.assertIn('@201.34.132.118:443?', key)
        self.assertNotIn('flow=xtls-rprx-vision', key)

    @staticmethod
    def async_run(coroutine):
        import asyncio

        return asyncio.run(coroutine)


class MirrorInboundTests(TestCase):
    """Verify enable/disable/add/remove propagate to mirror inbound ids."""

    def _client(self, api_class, mirror_ids):
        client = APIVPNClient(
            SimpleNamespace(
                vpn_url='',
                vpn_username='',
                vpn_password='',
                client_vpn_host='201.34.132.118:443',
                inbound_id=5,
            )
        )
        client._api.login = AsyncMock()
        api_class.return_value = client._api
        with patch('apps.servers.vpn_client.settings', SimpleNamespace(LIMIT_IP=2, MIRROR_INBOUND_IDS=mirror_ids)):
            yield client

    @patch('apps.servers.vpn_client.AsyncApi')
    def test_enable_propagates_to_mirror_inbounds(self, api_class):
        with patch('apps.servers.vpn_client.settings', SimpleNamespace(LIMIT_IP=2, MIRROR_INBOUND_IDS=[14])) as _settings:
            client = APIVPNClient(SimpleNamespace(vpn_url='', vpn_username='', vpn_password='', client_vpn_host='h:443', inbound_id=5))
            client._api = AsyncMock()
            client._api.login = AsyncMock()
            client._api.inbound.get_by_id = AsyncMock(return_value=SimpleNamespace(settings=SimpleNamespace(clients=[SimpleNamespace(id='uuid', enable=True, sub_id='', inbound_id=5)])))
            client._api.client.update = AsyncMock()
            user_vpn = SimpleNamespace(vpn_uuid='uuid', user=SimpleNamespace(telegram_id=1), server=client._server)
            self.async_run(client.enable_user(user_vpn, enabled=False))
            # primary inbound updated
            self.assertTrue(client._api.client.update.called)
            # mirror inbound fetched too
            self.assertGreaterEqual(client._api.inbound.get_by_id.call_count, 2)

    @staticmethod
    def async_run(coroutine):
        import asyncio
        return asyncio.run(coroutine)
