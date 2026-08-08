from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, patch

from django.test import override_settings

from apps.servers.subscription_connector import (
    SubscriptionConnectorDisabled,
    XUISubscriptionConnector,
    build_subscription_url,
)


class SubscriptionUrlTests(TestCase):
    def test_builds_https_subscription_url(self):
        self.assertEqual(
            build_subscription_url('https://sub.example.test/sub', 'abc123'),
            'https://sub.example.test/sub/abc123',
        )

    def test_rejects_non_https_origin(self):
        with self.assertRaises(ValueError):
            build_subscription_url('http://sub.example.test/sub', 'abc123')


class SubscriptionConnectorTests(TestCase):
    def setUp(self):
        self.server = SimpleNamespace(
            vpn_url='',
            vpn_username='',
            vpn_password='',
            inbound_id=5,
        )
        self.user_vpn = SimpleNamespace(vpn_uuid='vpn-uuid')

    @patch('apps.servers.subscription_connector.AsyncApi')
    @override_settings(SUBSCRIPTION_CONNECTOR_ENABLED=False, SUBSCRIPTION_BASE_URL='https://sub.example.test/sub')
    def test_disabled_connector_does_not_access_3x_ui(self, api_class):
        connector = XUISubscriptionConnector(self.server)

        with self.assertRaises(SubscriptionConnectorDisabled):
            self.async_run(connector.ensure_subscription_reference(self.user_vpn))

        connector._api.login.assert_not_called()
        api_class.assert_called_once()

    @patch('apps.servers.subscription_connector.AsyncApi')
    @override_settings(SUBSCRIPTION_CONNECTOR_ENABLED=True, SUBSCRIPTION_BASE_URL='https://sub.example.test/sub')
    def test_reads_existing_sub_id_without_mutation(self, _api_class):
        connector = XUISubscriptionConnector(self.server)
        client = SimpleNamespace(id='vpn-uuid', sub_id='existing-sub-id', enable=True)
        connector._api.login = AsyncMock()
        connector._api.inbound.get_by_id = AsyncMock(
            return_value=SimpleNamespace(settings=SimpleNamespace(clients=[client]))
        )
        connector._api.client.update = AsyncMock()

        reference = self.async_run(connector.get_existing_subscription_reference(self.user_vpn))

        self.assertEqual(reference.url, 'https://sub.example.test/sub/existing-sub-id')
        connector._api.client.update.assert_not_called()

    @patch('apps.servers.subscription_connector.token_hex', return_value='generated-sub-id')
    @patch('apps.servers.subscription_connector.AsyncApi')
    @override_settings(SUBSCRIPTION_CONNECTOR_ENABLED=True, SUBSCRIPTION_BASE_URL='https://sub.example.test/sub')
    def test_enabled_connector_assigns_sub_id_without_changing_enable(self, _api_class, _token_hex):
        connector = XUISubscriptionConnector(self.server)
        client = SimpleNamespace(id='vpn-uuid', sub_id='', enable=True, expiry_time=0)
        connector._api.login = AsyncMock()
        connector._api.inbound.get_by_id = AsyncMock(
            return_value=SimpleNamespace(settings=SimpleNamespace(clients=[client]))
        )
        connector._api.client.update = AsyncMock()

        reference = self.async_run(connector.ensure_subscription_reference(self.user_vpn))

        self.assertEqual(reference.sub_id, 'generated-sub-id')
        self.assertEqual(reference.url, 'https://sub.example.test/sub/generated-sub-id')
        self.assertEqual(client.sub_id, 'generated-sub-id')
        self.assertTrue(client.enable)
        self.assertEqual(client.expiry_time, 0)
        connector._api.client.update.assert_awaited_once_with('vpn-uuid', client)

    @staticmethod
    def async_run(coroutine):
        import asyncio

        return asyncio.run(coroutine)
