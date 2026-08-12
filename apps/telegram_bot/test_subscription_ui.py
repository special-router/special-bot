from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from apps.analytics.balance_split import BalanceSplit
from apps.telegram_bot.handlers.profile import build_profile_screen
from apps.telegram_bot.handlers.show_keys import build_keys_screen


class AsyncItems:
    def __init__(self, items):
        self._items = items

    def __aiter__(self):
        async def iterate():
            for item in self._items:
                yield item

        return iterate()


class SubscriptionUiTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = SimpleNamespace(
            id=10,
            telegram_id=1001,
            balance=100,
            created_at=SimpleNamespace(strftime=lambda _fmt: '01.01.2026'),
        )
        self.connection = SimpleNamespace(
            server=SimpleNamespace(name='SPECIAL'),
            vpn_key='vless://legacy-rollback',
            enabled=True,
            created_at=SimpleNamespace(strftime=lambda _fmt: '01.01.2026'),
        )

    @patch('apps.telegram_bot.handlers.show_keys.get_reply_markup_manage_keys', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.show_keys.get_user_access_url', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.show_keys.UserVPN')
    async def test_manage_keys_shows_subscription_url_instead_of_legacy_key(
        self, user_vpn, get_access_url, get_markup
    ):
        user_vpn.objects.with_related_server.return_value.filter.return_value = AsyncItems([self.connection])
        get_access_url.return_value = 'https://sub.example.test/sub/stable'
        get_markup.return_value = object()

        message, _keyboard = await build_keys_screen(self.user)

        self.assertIn('https://sub.example.test/sub/stable', message)
        self.assertNotIn('vless://legacy-rollback', message)
        get_access_url.assert_awaited_once_with(self.connection)

    @patch('apps.telegram_bot.utils.split_balance', return_value=BalanceSplit())
    @patch('apps.telegram_bot.handlers.profile.get_reply_markup_profile', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.profile.UserVPN')
    @patch('apps.telegram_bot.handlers.profile.TelegramUser')
    async def test_profile_shows_neither_the_legacy_key_nor_a_second_copy_of_the_link(
        self, telegram_user, user_vpn, get_markup, _split
    ):
        """Ссылки подписок переехали на экран «Подписки» — здесь их быть не должно."""
        telegram_user.objects.annotate_balance.return_value.aget = AsyncMock(return_value=self.user)
        user_vpn.objects.filter_by_user.return_value.filter_by_enabled.return_value.acount = AsyncMock(return_value=1)
        get_markup.return_value = object()

        message, _keyboard = await build_profile_screen(self.user)

        self.assertNotIn('vless://legacy-rollback', message)
        self.assertNotIn('sub.example.test', message)
        self.assertIn('Активных подписок: 1', message)
