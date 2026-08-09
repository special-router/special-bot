from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from django.test import override_settings

from apps.vpn.services.subscription_delivery import (
    get_subscription_url,
    get_user_access_url,
    prepare_subscription_url,
)


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

    @override_settings(SUBSCRIPTION_DELIVERY_ENABLED=False)
    @patch('apps.vpn.services.subscription_delivery.prepare_subscription_url', new_callable=AsyncMock)
    async def test_access_url_uses_legacy_key_when_delivery_is_disabled(self, prepare_url):
        self.user_vpn.vpn_key = 'legacy-key'
        self.user_vpn.enabled = True

        url = await get_user_access_url(self.user_vpn)

        self.assertEqual(url, 'legacy-key')
        prepare_url.assert_not_awaited()

    @override_settings(SUBSCRIPTION_DELIVERY_ENABLED=True)
    @patch('apps.vpn.services.subscription_delivery.prepare_subscription_url', new_callable=AsyncMock)
    async def test_access_url_prepares_subscription_for_active_user(self, prepare_url):
        self.user_vpn.vpn_key = 'legacy-key'
        self.user_vpn.enabled = True
        prepare_url.return_value = 'https://sub.example.test/sub/prepared'

        url = await get_user_access_url(self.user_vpn)

        self.assertEqual(url, 'https://sub.example.test/sub/prepared')
        prepare_url.assert_awaited_once_with(self.user_vpn)

    @override_settings(SUBSCRIPTION_DELIVERY_ENABLED=True)
    @patch('apps.vpn.services.subscription_delivery.get_subscription_url', new_callable=AsyncMock)
    @patch('apps.vpn.services.subscription_delivery.prepare_subscription_url', new_callable=AsyncMock)
    async def test_access_url_only_reads_existing_subscription_for_disabled_user(
        self, prepare_url, get_url
    ):
        self.user_vpn.vpn_key = 'legacy-key'
        self.user_vpn.enabled = False
        get_url.return_value = 'https://sub.example.test/sub/existing'

        url = await get_user_access_url(self.user_vpn)

        self.assertEqual(url, 'https://sub.example.test/sub/existing')
        get_url.assert_awaited_once_with(self.user_vpn)
        prepare_url.assert_not_awaited()

    @override_settings(SUBSCRIPTION_DELIVERY_ENABLED=True)
    @patch('apps.vpn.services.subscription_delivery.logger.warning')
    @patch('apps.vpn.services.subscription_delivery.prepare_subscription_url', new_callable=AsyncMock)
    async def test_access_url_falls_back_without_logging_secret_values(self, prepare_url, warning):
        self.user_vpn.vpn_key = 'legacy-key'
        self.user_vpn.enabled = True
        prepare_url.side_effect = RuntimeError('secret panel path and bearer URL')

        url = await get_user_access_url(self.user_vpn)

        self.assertEqual(url, 'legacy-key')
        warning.assert_called_once_with('Subscription delivery fallback: %s', 'RuntimeError')
