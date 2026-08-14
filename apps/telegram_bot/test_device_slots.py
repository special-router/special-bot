import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import TransactionTestCase, override_settings

from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.payments.models import Transaction
from apps.servers.models import Server, TariffServer
from apps.subscriptions.models import SubscriptionDevice
from apps.telegram_bot.handlers.devices import add_device_slot, drop_device_slot, unbind_one_device
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


def _update(callback_data: str):
    query = SimpleNamespace(
        data=callback_data,
        from_user=SimpleNamespace(id=1001, username='client'),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    return SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=1001))


@override_settings(SUBSCRIPTION_DEVICE_LIMIT=2, SUBSCRIPTION_FREE_DEVICE_SLOTS=2)
class DeviceSlotTests(TransactionTestCase):
    """Кнопки регулируют места и устройства, а не число подписок."""

    def setUp(self):
        tariff = TariffServer.objects.create(name='сутки', price=Decimal('7.00'))
        self.server = Server.objects.create(
            name='NL', ip_address='192.0.2.10', ssh_username='x', ssh_password='x',
            vpn_username='x', vpn_password='x', vpn_key='x', inbound_id=5, tariff=tariff,
        )
        self.user = TelegramUser.objects.create(telegram_id=1001, username='client')
        Transaction.objects.create(
            user=self.user, amount=Decimal('100.00'), status=TransactionStatusChoices.SUCCESS,
            source=TransactionSourceChoices.MANUAL,
        )
        self.subscription = UserVPN.objects.create(user=self.user, server=self.server, enabled=True)

    def _run(self, handler, data):
        with patch('apps.telegram_bot.handlers.devices.render_screen', new_callable=AsyncMock), \
                patch('apps.telegram_bot.handlers.show_keys.get_user_access_url',
                      new_callable=AsyncMock, return_value='https://sub.example.test/sub/x'):
            asyncio.run(handler(_update(data), None))

    def _limit(self):
        return UserVPN.objects.get(id=self.subscription.id).device_limit

    def test_buying_a_slot_raises_the_limit_and_charges_one_day(self):
        self._run(add_device_slot, 'add_device_slot')

        self.assertEqual(self._limit(), 3)
        self.assertEqual(Transaction.objects.filter(source=TransactionSourceChoices.BUY).count(), 1)
        self.assertEqual(
            Transaction.objects.filter(source=TransactionSourceChoices.BUY).get().amount, Decimal('-7.00'))

    def test_a_free_slot_cannot_be_dropped(self):
        """Бесплатные места входят в подписку — убрать их значит подешеветь даром."""
        self._run(drop_device_slot, 'drop_device_slot')

        self.assertIsNone(self._limit())

    def test_a_bought_slot_is_dropped_and_stops_being_billed(self):
        self._run(add_device_slot, 'add_device_slot')
        self._run(drop_device_slot, 'drop_device_slot')

        self.assertEqual(self._limit(), 2)

    def test_an_occupied_slot_is_not_dropped_while_a_device_holds_it(self):
        """Иначе клиент платил бы меньше, а пользовался по-прежнему — до отказа."""
        self._run(add_device_slot, 'add_device_slot')
        for index in range(3):
            SubscriptionDevice.objects.create(subscription=self.subscription, hwid=f'device-{index}0000000')

        update = _update('drop_device_slot')
        with patch('apps.telegram_bot.handlers.devices.render_screen', new_callable=AsyncMock):
            asyncio.run(drop_device_slot(update, None))

        self.assertEqual(self._limit(), 3)
        self.assertIn('отвяжите', update.callback_query.answer.await_args.kwargs['text'])

    def test_one_device_is_unbound_by_name_and_the_others_stay(self):
        kept = SubscriptionDevice.objects.create(subscription=self.subscription, hwid='keep-00000000')
        dropped = SubscriptionDevice.objects.create(subscription=self.subscription, hwid='drop-00000000')

        self._run(unbind_one_device, f'unbind_device:{dropped.id}')

        self.assertEqual(list(SubscriptionDevice.objects.values_list('id', flat=True)), [kept.id])

    def test_a_device_of_another_account_is_never_unbound(self):
        stranger = TelegramUser.objects.create(telegram_id=2002, username='stranger')
        their_subscription = UserVPN.objects.create(user=stranger, server=self.server, enabled=True)
        theirs = SubscriptionDevice.objects.create(subscription=their_subscription, hwid='theirs-000000')

        update = _update(f'unbind_device:{theirs.id}')
        with patch('apps.telegram_bot.handlers.devices.render_screen', new_callable=AsyncMock):
            asyncio.run(unbind_one_device(update, None))

        self.assertTrue(SubscriptionDevice.objects.filter(id=theirs.id).exists())
        self.assertIn('не найдено', update.callback_query.answer.await_args.kwargs['text'])

    def test_a_slot_is_not_sold_on_a_balance_that_cannot_pay_for_it(self):
        Transaction.objects.create(
            user=self.user, amount=Decimal('-96.00'), status=TransactionStatusChoices.SUCCESS,
            source=TransactionSourceChoices.MANUAL,
        )

        with patch('apps.telegram_bot.handlers.devices.render_screen', new_callable=AsyncMock), \
                patch('apps.telegram_bot.handlers.devices.build_balance_screen',
                      new_callable=AsyncMock, return_value=('balance', None)):
            asyncio.run(add_device_slot(_update('add_device_slot'), None))

        self.assertIsNone(self._limit())
        self.assertFalse(Transaction.objects.filter(source=TransactionSourceChoices.BUY).exists())
