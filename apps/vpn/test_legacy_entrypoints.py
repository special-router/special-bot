from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import override_settings

from apps.subscriptions.tasks import update_user_vpn
from apps.telegram_bot.handlers.add_key import add_key


class LegacyBillingTaskTests(TestCase):
    @patch('apps.subscriptions.tasks.time.sleep')
    @patch('apps.subscriptions.tasks.Bot')
    @patch('apps.subscriptions.tasks.disable_vpn_user_from_server', new_callable=AsyncMock)
    @patch('apps.subscriptions.tasks.Transaction.objects')
    @patch('apps.subscriptions.tasks.UserVPN.objects')
    def test_daily_billing_uses_server_tariff_and_disables_without_delete(
        self,
        user_vpn_objects,
        transaction_objects,
        disable_user,
        bot_class,
        sleep,
    ):
        tariff = SimpleNamespace(price=7)
        user = SimpleNamespace(balance=13, telegram_id=1001)
        user_vpn = SimpleNamespace(user=user, server=SimpleNamespace(tariff=tariff))
        related = user_vpn_objects.with_related_user.return_value.with_related_server.return_value
        enabled_query = related.filter_by_enabled.return_value
        enabled_query.__iter__.return_value = [user_vpn]
        bot_class.return_value.send_message = AsyncMock()

        update_user_vpn()

        transaction_objects.create.assert_called_once()
        self.assertEqual(transaction_objects.create.call_args.kwargs['amount'], -tariff.price)
        disable_user.assert_awaited_once_with(user_vpn)
        sleep.assert_called_once_with(1)


class AddKeyLimitTests(IsolatedAsyncioTestCase):
    @override_settings(MAX_KEYS=2)
    @patch('apps.telegram_bot.handlers.add_key.UserVPN.objects')
    @patch('apps.telegram_bot.handlers.add_key.redis.from_url')
    @patch('apps.telegram_bot.handlers.add_key.Server.objects')
    @patch('apps.telegram_bot.handlers.add_key.get_user', new_callable=AsyncMock)
    async def test_disabled_keys_do_not_count_toward_limit(
        self,
        get_user,
        server_objects,
        redis_from_url,
        user_vpn_objects,
    ):
        user = SimpleNamespace(id=10, balance=100)
        server = SimpleNamespace(tariff=SimpleNamespace(price=7))
        get_user.return_value = user
        server_objects.with_related_tariffs.return_value.order_by_random.return_value.afirst = AsyncMock(
            return_value=server
        )
        redis_client = redis_from_url.return_value
        redis_client.get.return_value = None
        active_query = user_vpn_objects.filter_by_user.return_value.filter_by_enabled.return_value
        active_query.acount = AsyncMock(return_value=2)
        callback_query = SimpleNamespace(
            data='add_key:123',
            answer=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=callback_query)

        await add_key(update, MagicMock())

        user_vpn_objects.filter_by_user.return_value.filter_by_enabled.assert_called_once_with(True)
        callback_query.answer.assert_awaited_once()
        self.assertIn('максимальное количество', callback_query.answer.await_args.kwargs['text'])
