from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from apps.servers.subscription_connector import SubscriptionClientMissing
from apps.telegram_bot.handlers.subscription import show_subscription


class SubscriptionHandlerTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = SimpleNamespace(telegram_id=123)
        self.context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        self.update = SimpleNamespace()

    @patch('apps.telegram_bot.handlers.subscription.get_user', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.subscription.UserVPN.objects')
    async def test_preserves_legacy_path_when_no_active_connection(self, user_vpn_objects, get_user):
        get_user.return_value = self.user
        query = user_vpn_objects.with_related_server.return_value.filter.return_value.order_by.return_value
        query.afirst = AsyncMock(return_value=None)

        await show_subscription(self.update, self.context)

        self.context.bot.send_message.assert_awaited_once_with(123, text='У вас нет активной подписки.')

    @patch('apps.telegram_bot.handlers.subscription.get_subscription_url', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.subscription.get_user', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.subscription.UserVPN.objects')
    async def test_returns_existing_url_without_mutating_connection(
        self, user_vpn_objects, get_user, get_subscription_url
    ):
        get_user.return_value = self.user
        connection = SimpleNamespace()
        query = user_vpn_objects.with_related_server.return_value.filter.return_value.order_by.return_value
        query.afirst = AsyncMock(return_value=connection)
        get_subscription_url.return_value = 'https://sub.example.test/sub/existing'

        await show_subscription(self.update, self.context)

        get_subscription_url.assert_awaited_once_with(connection)
        self.context.bot.send_message.assert_awaited_once_with(
            123,
            text='URL подписки (автообновление конфигурации):\n\nhttps://sub.example.test/sub/existing',
        )

    @patch('apps.telegram_bot.handlers.subscription.get_subscription_url', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.subscription.get_user', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.subscription.UserVPN.objects')
    async def test_falls_back_to_legacy_key_when_reference_is_not_ready(
        self, user_vpn_objects, get_user, get_subscription_url
    ):
        get_user.return_value = self.user
        query = user_vpn_objects.with_related_server.return_value.filter.return_value.order_by.return_value
        query.afirst = AsyncMock(return_value=SimpleNamespace())
        get_subscription_url.side_effect = SubscriptionClientMissing('not ready')

        await show_subscription(self.update, self.context)

        self.context.bot.send_message.assert_awaited_once_with(
            123,
            text='Подписка ещё не подготовлена. Используйте выданный VLESS-ключ.',
        )
