from datetime import date
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from apps.telegram_bot.handlers.profile import show_profile
from apps.telegram_bot.handlers.show_keys import show_keys


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
        self.user = SimpleNamespace(id=10, telegram_id=1001, created_at=SimpleNamespace(strftime=lambda _fmt: '01.01.2026'))
        self.connection = SimpleNamespace(
            server=SimpleNamespace(name='SPECIAL'),
            vpn_key='vless://legacy-rollback',
            enabled=True,
            created_at=SimpleNamespace(date=lambda: date(2026, 1, 1)),
        )
        self.context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        self.update = SimpleNamespace()

    @patch('apps.telegram_bot.handlers.show_keys.get_reply_markup_manage_keys', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.show_keys.get_user_access_url', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.show_keys.get_user', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.show_keys.UserVPN.objects')
    async def test_manage_keys_shows_subscription_url_instead_of_legacy_key(
        self, objects, get_user, get_access_url, get_markup
    ):
        get_user.return_value = self.user
        objects.with_related_server.return_value.filter.return_value = AsyncItems([self.connection])
        get_access_url.return_value = 'https://sub.example.test/sub/stable'
        get_markup.return_value = object()

        await show_keys(self.update, self.context)

        message = self.context.bot.send_message.await_args.kwargs['text']
        self.assertIn('https://sub.example.test/sub/stable', message)
        self.assertNotIn('vless://legacy-rollback', message)
        get_access_url.assert_awaited_once_with(self.connection)

    @patch('apps.telegram_bot.handlers.profile.get_reply_markup_profile', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.profile.get_user_access_url', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.profile.get_user', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.profile.UserVPN.objects')
    @patch('apps.telegram_bot.handlers.profile.TelegramUser.objects')
    async def test_profile_shows_subscription_url_instead_of_legacy_key(
        self, telegram_users, user_vpns, get_user, get_access_url, get_markup
    ):
        get_user.return_value = self.user
        hydrated_user = SimpleNamespace(balance=100)
        telegram_users.annotate_balance.return_value.aget = AsyncMock(return_value=hydrated_user)
        user_vpns.with_related_server.return_value.filter.return_value = [self.connection]
        get_access_url.return_value = 'https://sub.example.test/sub/stable'
        get_markup.return_value = object()

        await show_profile(self.update, self.context)

        message = self.context.bot.send_message.await_args.kwargs['text']
        self.assertIn('https://sub.example.test/sub/stable', message)
        self.assertNotIn('vless://legacy-rollback', message)
        get_access_url.assert_awaited_once_with(self.connection)
