from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from django.test import override_settings

from apps.servers.management.commands.audit_xui_inbounds import fetch_inbound_snapshots


class InboundAuditTests(IsolatedAsyncioTestCase):
    @override_settings(XUI_CONTROL_PLANE_READ_ATTEMPTS=2, XUI_CONTROL_PLANE_READ_BACKOFF=0)
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
        self.assertEqual(api.inbound.get_list.await_count, 2)

    @override_settings(XUI_CONTROL_PLANE_READ_ATTEMPTS=4, XUI_CONTROL_PLANE_READ_BACKOFF=0)
    @patch('apps.servers.management.commands.audit_xui_inbounds.AsyncApi')
    async def test_inventory_accepts_short_then_two_matching_full_snapshots(self, api_class):
        server = SimpleNamespace(id=1, name='NL', vpn_url='', vpn_username='', vpn_password='')
        short = SimpleNamespace(
            id=5,
            port=8443,
            protocol='vless',
            settings=SimpleNamespace(clients=[SimpleNamespace(enable=True, sub_id='one')]),
            stream_settings=SimpleNamespace(network='tcp', security='reality'),
        )
        full = SimpleNamespace(
            id=5,
            port=8443,
            protocol='vless',
            settings=SimpleNamespace(
                clients=[SimpleNamespace(enable=True, sub_id='one'), SimpleNamespace(enable=True, sub_id='two')]
            ),
            stream_settings=SimpleNamespace(network='tcp', security='reality'),
        )
        api = api_class.return_value
        api.login = AsyncMock()
        api.inbound.get_list = AsyncMock(side_effect=[[short], [full], [full]])

        snapshots = await fetch_inbound_snapshots(server)

        self.assertEqual(snapshots[0].clients, 2)
        self.assertEqual(api.inbound.get_list.await_count, 3)

    @override_settings(XUI_CONTROL_PLANE_READ_ATTEMPTS=4, XUI_CONTROL_PLANE_READ_BACKOFF=0)
    @patch('apps.servers.management.commands.audit_xui_inbounds.AsyncApi')
    async def test_inventory_fails_closed_when_snapshots_never_stabilize(self, api_class):
        server = SimpleNamespace(id=1, name='NL', vpn_url='', vpn_username='', vpn_password='')
        api = api_class.return_value
        api.login = AsyncMock()
        api.inbound.get_list = AsyncMock(
            side_effect=[
                [
                    SimpleNamespace(
                        id=5,
                        port=8443,
                        protocol='vless',
                        settings=SimpleNamespace(
                            clients=[SimpleNamespace(enable=True, sub_id=str(index)) for index in range(count)]
                        ),
                        stream_settings=SimpleNamespace(network='tcp', security='reality'),
                    )
                ]
                for count in range(1, 5)
            ]
        )

        with self.assertRaisesRegex(RuntimeError, 'inventory consistency'):
            await fetch_inbound_snapshots(server)

        self.assertEqual(api.inbound.get_list.await_count, 4)
