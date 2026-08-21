from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from django.test import override_settings

from apps.servers.subscription_connector import SubscriptionClientMissing
from apps.vpn.services.subscription_delivery import (
    get_subscription_url,
    get_user_access_url,
    prepare_subscription_url,
)


class SubscriptionDeliveryTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.user_vpn = SimpleNamespace(server=SimpleNamespace())

    @override_settings(REMNAWAVE_ENABLED=False)
    @patch('apps.vpn.services.subscription_delivery.XUISubscriptionConnector')
    async def test_get_uses_read_only_connector_method(self, connector_class):
        connector_class.return_value.get_existing_subscription_reference = AsyncMock(
            return_value=SimpleNamespace(url='https://sub.example.test/sub/existing')
        )

        url = await get_subscription_url(self.user_vpn)

        self.assertEqual(url, 'https://sub.example.test/sub/existing')
        connector_class.return_value.ensure_subscription_reference.assert_not_called()

    @override_settings(REMNAWAVE_ENABLED=False)
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


_PANEL = dict(REMNAWAVE_ENABLED=True, REMNAWAVE_API_URL='https://panel.test',
              REMNAWAVE_API_TOKEN='t' * 32, SUBSCRIPTION_BASE_URL='https://sub.example.test/sub')


class RemnawaveDeliveryTests(IsolatedAsyncioTestCase):
    """С включённой панелью выдача не должна ходить в остановленный 3x-ui.

    Это не теория: после переключения ``get_user_access_url`` падал в fallback
    на каждой выдаче, и клиент получал прямой ключ — один сервер вместо всего
    списка. Видно это было только по строке в логе.
    """

    def _user_vpn(self, sub_id='abcdef0123456789'):
        return SimpleNamespace(
            server=SimpleNamespace(), sub_id=sub_id, enabled=True, id=801,
            vpn_key='vless://legacy', user=SimpleNamespace(telegram_id=1),
        )

    @override_settings(**_PANEL)
    @patch('apps.vpn.services.subscription_delivery.XUISubscriptionConnector')
    @patch('apps.servers.remnawave_subscription.RemnawaveAPI')
    async def test_prepare_builds_the_link_without_touching_the_old_panel(
        self, api_class, connector_class,
    ):
        api_class.return_value.get_user_by_username = AsyncMock(return_value={'id': 7})

        url = await prepare_subscription_url(self._user_vpn())

        self.assertEqual(url, 'https://sub.example.test/sub/abcdef0123456789')
        connector_class.assert_not_called()

    @override_settings(**_PANEL)
    @patch('apps.vpn.services.subscription_delivery.XUISubscriptionConnector')
    async def test_read_only_path_needs_no_panel_call_at_all(self, connector_class):
        url = await get_subscription_url(self._user_vpn())

        self.assertEqual(url, 'https://sub.example.test/sub/abcdef0123456789')
        connector_class.assert_not_called()

    @override_settings(**_PANEL)
    @patch('apps.servers.remnawave_subscription.RemnawaveAPI')
    async def test_client_unknown_to_the_panel_is_reported_not_papered_over(self, api_class):
        api_class.return_value.get_user_by_username = AsyncMock(return_value=None)

        with self.assertRaises(SubscriptionClientMissing):
            await prepare_subscription_url(self._user_vpn())

    @override_settings(**_PANEL)
    @patch('apps.servers.remnawave_subscription.RemnawaveAPI')
    async def test_missing_sub_id_is_reported_instead_of_inventing_one(self, api_class):
        """shortUuid в Remnawave задаётся только при создании — присвоить нельзя."""
        with self.assertRaises(SubscriptionClientMissing):
            await prepare_subscription_url(self._user_vpn(sub_id=''))

        api_class.return_value.get_user_by_username.assert_not_called()

    @override_settings(**_PANEL, SUBSCRIPTION_DELIVERY_ENABLED=True)
    @patch('apps.servers.remnawave_subscription.RemnawaveAPI')
    async def test_active_customer_gets_the_subscription_not_the_direct_key(self, api_class):
        api_class.return_value.get_user_by_username = AsyncMock(return_value={'id': 7})

        url = await get_user_access_url(self._user_vpn())

        self.assertTrue(url.startswith('https://'))
        self.assertNotEqual(url, 'vless://legacy')
