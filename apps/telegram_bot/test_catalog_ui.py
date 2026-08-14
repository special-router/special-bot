from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from apps.subscriptions.catalog import SubscriptionCatalog
from apps.telegram_bot.catalog import catalog_body
from apps.telegram_bot.handlers.main_menu import build_main_menu_screen
from apps.telegram_bot.handlers.show_keys import build_keys_screen


class AsyncItems:
    def __init__(self, items):
        self._items = items

    def __aiter__(self):
        async def iterate():
            for item in self._items:
                yield item

        return iterate()


FULL = SubscriptionCatalog(
    countries=('🇳🇱 Нидерланды', '🇩🇪 Германия', '🇯🇵 Япония'),
    whitelisted=('🇳🇱 Нидерланды белые списки',),
)


class CatalogBodyTests(IsolatedAsyncioTestCase):
    def test_countries_and_the_bypass_line_are_separate_blocks(self):
        blocks = catalog_body(FULL)

        self.assertEqual(blocks[0], 'Страны в подписке: 🇳🇱 Нидерланды, 🇩🇪 Германия, 🇯🇵 Япония.')
        self.assertIn('🇳🇱 Нидерланды белые списки', blocks[1])
        self.assertIn('мобильный интернет', blocks[1])

    def test_a_catalog_without_a_bypass_line_says_nothing_about_one(self):
        blocks = catalog_body(SubscriptionCatalog(countries=('🇳🇱 Нидерланды',)))

        self.assertEqual(len(blocks), 1)
        self.assertNotIn('белые списки', blocks[0])

    def test_an_empty_catalog_promises_nothing_at_all(self):
        self.assertEqual(catalog_body(SubscriptionCatalog()), [])


class CatalogScreenTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = SimpleNamespace(id=10, telegram_id=1001, balance=100)
        self.connection = SimpleNamespace(
            id=808,
            server=SimpleNamespace(name='Нидерланды', client_vpn_host='relay.example:443'),
            enabled=True,
            created_at=SimpleNamespace(strftime=lambda _fmt: '01.01.2026'),
        )

    @patch('apps.telegram_bot.handlers.main_menu.get_reply_markup_main_menu', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.main_menu.acatalog', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.main_menu.UserVPN')
    async def test_the_main_menu_names_every_country_before_a_purchase(
        self, user_vpn, catalog, get_markup
    ):
        user_vpn.objects.filter_by_user.return_value.filter_by_enabled.return_value.acount = AsyncMock(
            return_value=0)
        catalog.return_value = FULL
        get_markup.return_value = object()

        message, _keyboard = await build_main_menu_screen(self.user, greeting=True)

        self.assertIn('🇩🇪 Германия', message)
        self.assertIn('🇯🇵 Япония', message)
        self.assertIn('🇳🇱 Нидерланды белые списки', message)
        # Каталог витрины описывает подписку, которой ещё нет: она без id.
        catalog.assert_awaited_once_with()

    @patch('apps.telegram_bot.handlers.main_menu.get_reply_markup_main_menu', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.main_menu.acatalog', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.main_menu.UserVPN')
    async def test_the_main_menu_survives_a_catalog_that_knows_nothing(
        self, user_vpn, catalog, get_markup
    ):
        user_vpn.objects.filter_by_user.return_value.filter_by_enabled.return_value.acount = AsyncMock(
            return_value=0)
        catalog.return_value = SubscriptionCatalog()
        get_markup.return_value = object()

        message, _keyboard = await build_main_menu_screen(self.user)

        self.assertIn('SPECIAL VPN', message)
        self.assertNotIn('Страны в подписке', message)

    @patch('apps.telegram_bot.handlers.show_keys.get_reply_markup_manage_keys', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.show_keys.get_user_access_url', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.show_keys.acatalog', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.show_keys.UserVPN')
    async def test_the_keys_screen_lists_countries_instead_of_naming_one_server(
        self, user_vpn, catalog, get_access_url, get_markup
    ):
        user_vpn.objects.with_related_server.return_value.filter.return_value = AsyncItems([self.connection])
        get_access_url.return_value = 'https://sub.example.test/sub/stable'
        catalog.return_value = FULL
        get_markup.return_value = object()

        message, _keyboard = await build_keys_screen(self.user)

        self.assertIn('Страны в подписке:', message)
        self.assertIn('🇩🇪 Германия', message)
        # Имя сервера называло одну страну из девяти — строка подписки о стране
        # больше не заявляет ничего.
        self.assertNotIn('✅ Нидерланды, с', message)
        self.assertIn('✅ Подписка, с 01.01.2026', message)
        catalog.assert_awaited_once_with(self.connection)

    @patch('apps.telegram_bot.handlers.show_keys.get_reply_markup_manage_keys', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.show_keys.get_user_access_url', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.show_keys.acatalog', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.show_keys.UserVPN')
    async def test_a_customer_without_a_subscription_is_still_told_what_it_contains(
        self, user_vpn, catalog, _get_access_url, get_markup
    ):
        user_vpn.objects.with_related_server.return_value.filter.return_value = AsyncItems([])
        catalog.return_value = FULL
        get_markup.return_value = object()

        message, _keyboard = await build_keys_screen(self.user)

        self.assertIn('Страны в подписке:', message)
        catalog.assert_awaited_once_with(None)
