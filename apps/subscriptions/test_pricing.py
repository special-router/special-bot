from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import TestCase, override_settings

from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.payments.models import Transaction
from apps.servers.models import Server, TariffServer
from apps.subscriptions.pricing import daily_price, paid_device_slots
from apps.subscriptions.tasks import update_user_vpn
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


def _subscription(device_limit=None, price='7.00'):
    return SimpleNamespace(
        device_limit=device_limit,
        server=SimpleNamespace(tariff=SimpleNamespace(price=Decimal(price))),
    )


@override_settings(SUBSCRIPTION_DEVICE_LIMIT=2, SUBSCRIPTION_FREE_DEVICE_SLOTS=2)
class DevicePricingTests(TestCase):
    """Места, входящие в тариф, бесплатны; каждое следующее стоит ещё тариф."""

    def test_a_subscription_on_the_default_limit_costs_exactly_the_tariff(self):
        self.assertEqual(paid_device_slots(_subscription()), 0)
        self.assertEqual(daily_price(_subscription()), Decimal('7.00'))

    def test_every_slot_above_the_free_ones_adds_a_tariff_a_day(self):
        self.assertEqual(daily_price(_subscription(device_limit=3)), Decimal('14.00'))
        self.assertEqual(daily_price(_subscription(device_limit=4)), Decimal('21.00'))

    def test_a_limit_below_the_free_allowance_never_makes_it_cheaper(self):
        """Иначе клиент удешевлял бы подписку, отказываясь от того, что и так входит."""
        self.assertEqual(daily_price(_subscription(device_limit=1)), Decimal('7.00'))


@override_settings(SUBSCRIPTION_DEVICE_LIMIT=2, SUBSCRIPTION_FREE_DEVICE_SLOTS=2)
class DailyBillingWithSlotsTests(TestCase):
    def setUp(self):
        tariff = TariffServer.objects.create(name='сутки', price=Decimal('7.00'))
        self.server = Server.objects.create(
            name='NL', ip_address='192.0.2.10', ssh_username='x', ssh_password='x',
            vpn_username='x', vpn_password='x', vpn_key='x', inbound_id=5, tariff=tariff,
        )

    def _user(self, telegram_id, balance):
        user = TelegramUser.objects.create(telegram_id=telegram_id, username=f'u{telegram_id}')
        Transaction.objects.create(
            user=user, amount=Decimal(balance), status=TransactionStatusChoices.SUCCESS,
            source=TransactionSourceChoices.MANUAL,
        )
        return user

    @patch('apps.subscriptions.tasks.time.sleep', MagicMock())
    @patch('apps.subscriptions.tasks.Bot')
    @patch('apps.subscriptions.tasks.disable_vpn_user_from_server', new_callable=AsyncMock)
    def test_the_daily_charge_follows_the_bought_slots(self, _disable, bot_class):
        bot_class.return_value.send_message = AsyncMock()
        user = self._user(2001, '100.00')
        subscription = UserVPN.objects.create(user=user, server=self.server, device_limit=4)

        update_user_vpn()

        charge = Transaction.objects.filter_by_source(TransactionSourceChoices.EVERYDAY_SYSTEM).get()
        self.assertEqual(charge.user_vpn_id, subscription.id)
        self.assertEqual(charge.amount, Decimal('-21.00'))

    @patch('apps.subscriptions.tasks.time.sleep', MagicMock())
    @patch('apps.subscriptions.tasks.Bot')
    @patch('apps.subscriptions.tasks.disable_vpn_user_from_server', new_callable=AsyncMock)
    def test_slots_a_balance_cannot_cover_disable_the_subscription(self, disable, bot_class):
        """Раньше решение принималось по тарифу, а списывалось бы больше него."""
        bot_class.return_value.send_message = AsyncMock()
        user = self._user(2002, '10.00')
        subscription = UserVPN.objects.create(user=user, server=self.server, device_limit=4)

        update_user_vpn()

        self.assertFalse(Transaction.objects.filter_by_source(TransactionSourceChoices.EVERYDAY_SYSTEM).exists())
        disable.assert_awaited_once()
        self.assertEqual(disable.await_args.args[0].id, subscription.id)
