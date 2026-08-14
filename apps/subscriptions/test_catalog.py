from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.subscriptions.catalog import SubscriptionCatalog, subscription_catalog


def _line(remark: str) -> str:
    """Строка подписки в том виде, в каком её собирает рендерер."""
    from urllib.parse import quote
    return f'vless://uuid@host.example:443?type=tcp&security=reality#{quote(remark)}'


ROLLED_OUT = {
    'SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED': True,
    'SUBSCRIPTION_BACKUP_ALL_USERS_ENABLED': True,
}


class SubscriptionCatalogTests(SimpleTestCase):
    def setUp(self):
        self.connection = SimpleNamespace(
            id=808,
            server=SimpleNamespace(client_vpn_host='relay.example:443'),
        )

    @override_settings(**ROLLED_OUT)
    @patch('apps.subscriptions.catalog._backup_links')
    def test_countries_follow_the_order_of_the_delivered_lines(self, backup_links):
        backup_links.return_value = [_line('🇩🇪 Германия'), _line('🇯🇵 Япония')]

        catalog = subscription_catalog(self.connection)

        self.assertEqual(
            catalog.countries,
            ('🇳🇱 Нидерланды', '🇩🇪 Германия', '🇯🇵 Япония'),
        )

    @override_settings(**ROLLED_OUT)
    @patch('apps.subscriptions.catalog._backup_links')
    def test_the_disambiguating_number_does_not_become_a_second_country(self, backup_links):
        backup_links.return_value = [_line('🇳🇱 Нидерланды 2'), _line('🇩🇪 Германия')]

        catalog = subscription_catalog(self.connection)

        self.assertEqual(catalog.countries, ('🇳🇱 Нидерланды', '🇩🇪 Германия'))

    @override_settings(**ROLLED_OUT)
    @patch('apps.subscriptions.catalog._backup_links')
    def test_a_whitelist_line_keeps_its_full_label_and_still_counts_as_a_country(self, backup_links):
        backup_links.return_value = []

        catalog = subscription_catalog(self.connection)

        self.assertEqual(catalog.whitelisted, ('🇳🇱 Нидерланды белые списки',))
        self.assertEqual(catalog.countries, ('🇳🇱 Нидерланды',))

    @override_settings(**ROLLED_OUT)
    @patch('apps.subscriptions.catalog._backup_links')
    def test_a_server_without_a_relay_promises_no_bypass(self, backup_links):
        backup_links.return_value = []
        connection = SimpleNamespace(id=808, server=SimpleNamespace(client_vpn_host=''))

        catalog = subscription_catalog(connection)

        self.assertEqual(catalog.whitelisted, ())
        self.assertEqual(catalog.countries, ('🇳🇱 Нидерланды',))

    @override_settings(SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=False)
    @patch('apps.subscriptions.catalog._backup_links')
    def test_a_subscription_outside_the_rollout_is_promised_only_our_own_lines(self, backup_links):
        backup_links.return_value = [_line('🇩🇪 Германия')]

        catalog = subscription_catalog(self.connection)

        self.assertEqual(catalog.countries, ('🇳🇱 Нидерланды',))
        backup_links.assert_not_called()

    @override_settings(
        SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=True,
        SUBSCRIPTION_BACKUP_ALL_USERS_ENABLED=False,
        SUBSCRIPTION_BACKUP_TEST_USER_IDS=[808],
    )
    @patch('apps.subscriptions.catalog._relay_configured', return_value=False)
    @patch('apps.subscriptions.catalog._backup_links')
    def test_an_allowlist_rollout_promises_a_new_subscription_nothing_it_would_not_get(
        self, backup_links, _relay
    ):
        """У подписки, которой ещё нет, нет id — а список выкатки отвечает про id."""
        backup_links.return_value = [_line('🇩🇪 Германия')]

        self.assertEqual(subscription_catalog().countries, ('🇳🇱 Нидерланды',))
        self.assertEqual(
            subscription_catalog(self.connection).countries,
            ('🇳🇱 Нидерланды', '🇩🇪 Германия'),
        )

    @override_settings(**ROLLED_OUT)
    @patch('apps.subscriptions.catalog._backup_links', side_effect=OSError('provider unreachable'))
    def test_a_failure_costs_the_promise_and_never_the_screen(self, _backup_links):
        self.assertEqual(subscription_catalog(self.connection), SubscriptionCatalog())

    @override_settings(**ROLLED_OUT)
    @patch('apps.subscriptions.catalog._backup_links')
    def test_an_unlabelled_provider_endpoint_is_shown_as_the_client_sees_it(self, backup_links):
        """Клиент читает «🌐 Резерв» в своём приложении — экран не вправе звать её иначе."""
        backup_links.return_value = [_line('🌐 Резерв')]

        self.assertIn('🌐 Резерв', subscription_catalog(self.connection).countries)
