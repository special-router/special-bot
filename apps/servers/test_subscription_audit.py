from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, patch

from apps.servers.management.commands.audit_xui_subscription import fetch_subscription_client_counts


class SubscriptionAuditTests(TestCase):
    @patch('apps.servers.management.commands.audit_xui_subscription.AsyncApi')
    def test_counts_subscription_ids_without_mutation(self, _api_class):
        server = SimpleNamespace(vpn_url='', vpn_username='', vpn_password='', inbound_id=5)
        clients = [
            SimpleNamespace(sub_id='existing', enable=True),
            SimpleNamespace(sub_id='', enable=True),
            SimpleNamespace(sub_id='', enable=False),
        ]
        with patch('apps.servers.management.commands.audit_xui_subscription.AsyncApi') as api_class:
            api = api_class.return_value
            api.login = AsyncMock()
            api.inbound.get_by_id = AsyncMock(return_value=SimpleNamespace(settings=SimpleNamespace(clients=clients)))

            counts = self.async_run(fetch_subscription_client_counts(server))

        self.assertEqual(counts.total, 3)
        self.assertEqual(counts.enabled, 2)
        self.assertEqual(counts.with_sub_id, 1)
        api.client.update.assert_not_called()

    @staticmethod
    def async_run(coroutine):
        import asyncio

        return asyncio.run(coroutine)
