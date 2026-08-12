from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from apps.servers.subscription_connector import SubscriptionClientMissing
from apps.telegram_bot.handlers.subscription import build_subscription_screen


class SubscriptionHandlerTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = SimpleNamespace(telegram_id=123)

    @patch('apps.telegram_bot.handlers.subscription.get_reply_markup_back', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.subscription.UserVPN')
    async def test_preserves_legacy_path_when_no_active_connection(self, user_vpn, _get_markup):
        query = user_vpn.objects.with_related_server.return_value.filter.return_value.order_by.return_value
        query.afirst = AsyncMock(return_value=None)

        message, _keyboard = await build_subscription_screen(self.user)

        self.assertIn('У вас нет активной подписки.', message)

    @patch('apps.telegram_bot.handlers.subscription.get_reply_markup_back', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.subscription.get_subscription_url', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.subscription.UserVPN')
    async def test_returns_existing_url_without_mutating_connection(
        self, user_vpn, get_subscription_url, _get_markup
    ):
        connection = SimpleNamespace()
        query = user_vpn.objects.with_related_server.return_value.filter.return_value.order_by.return_value
        query.afirst = AsyncMock(return_value=connection)
        get_subscription_url.return_value = 'https://sub.example.test/sub/existing'

        message, _keyboard = await build_subscription_screen(self.user)

        get_subscription_url.assert_awaited_once_with(connection)
        self.assertIn('https://sub.example.test/sub/existing', message)

    @patch('apps.telegram_bot.handlers.subscription.get_reply_markup_back', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.subscription.get_subscription_url', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.subscription.UserVPN')
    async def test_falls_back_to_legacy_key_when_reference_is_not_ready(
        self, user_vpn, get_subscription_url, _get_markup
    ):
        query = user_vpn.objects.with_related_server.return_value.filter.return_value.order_by.return_value
        query.afirst = AsyncMock(return_value=SimpleNamespace())
        get_subscription_url.side_effect = SubscriptionClientMissing('not ready')

        message, _keyboard = await build_subscription_screen(self.user)

        self.assertIn('Подписка ещё не подготовлена. Используйте ранее выданную ссылку подключения.', message)
