from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from apps.servers.management.commands.audit_xui_inbounds import fetch_inbound_snapshots


class InboundAuditTests(IsolatedAsyncioTestCase):
    @patch('apps.servers.management.commands.audit_xui_inbounds.AsyncApi')
    async def test_inventory_contains_protocol_and_client_counts(self, api_class):
        server = SimpleNamespace(id=1, name='NL', vpn_url='', vpn_username='', vpn_password='')
        inbound = SimpleNamespace(
            id=5,
            port=8443,
            protocol='vless',
            settings=SimpleNamespace(
                clients=[
                    SimpleNamespace(enable=True, sub_id='canary'),
                    SimpleNamespace(enable=False, sub_id=''),
                ]
            ),
            stream_settings=SimpleNamespace(network='tcp', security='reality'),
        )
        api = api_class.return_value
        api.login = AsyncMock()
        api.inbound.get_list = AsyncMock(return_value=[inbound])

        snapshots = await fetch_inbound_snapshots(server)

        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].inbound_id, 5)
        self.assertEqual(snapshots[0].port, 8443)
        self.assertEqual(snapshots[0].clients, 2)
        self.assertEqual(snapshots[0].enabled_clients, 1)
        self.assertEqual(snapshots[0].with_sub_id, 1)
        self.assertEqual(snapshots[0].missing_sub_id, 1)
        api.client.update.assert_not_called()
