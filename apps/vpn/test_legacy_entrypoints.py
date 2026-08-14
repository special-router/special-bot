from decimal import Decimal
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import TestCase, override_settings

from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.payments.models import Transaction
from apps.servers.models import Server, TariffServer
from apps.subscriptions.tasks import update_user_vpn
from apps.telegram_bot.handlers.add_key import add_key
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


class LegacyBillingTaskTests(TestCase):
    def make_server(self, price: str) -> Server:
        return Server.objects.create(
            name=f'server-{price}',
            ip_address='127.0.0.1',
            ssh_username='unused',
            ssh_password='unused',
            vpn_username='unused',
            vpn_password='unused',
            vpn_key='unused',
            vpn_url='https://panel.invalid',
            client_vpn_host='127.0.0.1',
            tariff=TariffServer.objects.create(name=f'tariff-{price}', price=Decimal(price)),
        )

    def make_user(self, telegram_id: int, balance: str) -> TelegramUser:
        user = TelegramUser.objects.create(telegram_id=telegram_id, username=f'user{telegram_id}')
        Transaction.objects.create(
            user=user,
            amount=Decimal(balance),
            status=TransactionStatusChoices.SUCCESS,
            source=TransactionSourceChoices.MANUAL,
        )
        return user

    @patch('apps.subscriptions.tasks.time.sleep')
    @patch('apps.subscriptions.tasks.Bot')
    @patch('apps.subscriptions.tasks.disable_vpn_user_from_server', new_callable=AsyncMock)
    def test_daily_billing_uses_server_tariff_and_disables_without_delete(
        self,
        disable_user,
        bot_class,
        sleep,
    ):
        bot_class.return_value.send_message = AsyncMock()
        funded = self.make_user(1001, '30.00')
        funded_vpn = UserVPN.objects.create(user=funded, server=self.make_server('9.00'))
        broke = self.make_user(1002, '0.00')
        broke_vpn = UserVPN.objects.create(user=broke, server=self.make_server('11.00'))

        update_user_vpn()

        charge = Transaction.objects.filter_by_source(TransactionSourceChoices.EVERYDAY_SYSTEM).get()
        self.assertEqual(charge.user_vpn_id, funded_vpn.id)
        self.assertEqual(charge.amount, Decimal('-9.00'))
        disable_user.assert_awaited_once_with(broke_vpn)
        self.assertTrue(UserVPN.objects.filter_by_id(broke_vpn.id).exists())
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
        # Серверы, где у аккаунта уже есть работающая подписка, отсеиваются до
        # выбора: иначе нажатие списывало сутки и возвращало ту же подписку.
        server_objects.with_related_tariffs.return_value.exclude.return_value \
            .order_by_random.return_value.afirst = AsyncMock(return_value=server)
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

        user_vpn_objects.filter_by_user.return_value.filter_by_enabled.assert_called_with(True)
        callback_query.answer.assert_awaited_once()
        self.assertIn('максимальное количество', callback_query.answer.await_args.kwargs['text'])
