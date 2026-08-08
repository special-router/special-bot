from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from apps.vpn.services.subscription_delivery import get_subscription_url, prepare_subscription_url


class SubscriptionDeliveryTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.user_vpn = SimpleNamespace(server=SimpleNamespace())

    @patch('apps.vpn.services.subscription_delivery.XUISubscriptionConnector')
    async def test_get_uses_read_only_connector_method(self, connector_class):
        connector_class.return_value.get_existing_subscription_reference = AsyncMock(
            return_value=SimpleNamespace(url='https://sub.example.test/sub/existing')
        )

        url = await get_subscription_url(self.user_vpn)

        self.assertEqual(url, 'https://sub.example.test/sub/existing')
        connector_class.return_value.ensure_subscription_reference.assert_not_called()

    @patch('apps.vpn.services.subscription_delivery.XUISubscriptionConnector')
    async def test_prepare_uses_explicit_mutating_connector_method(self, connector_class):
        connector_class.return_value.ensure_subscription_reference = AsyncMock(
            return_value=SimpleNamespace(url='https://sub.example.test/sub/prepared')
        )

        url = await prepare_subscription_url(self.user_vpn)

        self.assertEqual(url, 'https://sub.example.test/sub/prepared')
        connector_class.return_value.get_existing_subscription_reference.assert_not_called()
