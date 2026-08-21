from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import TransactionTestCase, override_settings

from apps.payments.choices import TransactionSourceChoices
from apps.payments.models import Transaction
from apps.servers.models import Server, TariffServer
from apps.telegram_bot.handlers.add_key import add_key
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


def _update(callback_data: str = 'add_key:1'):
    """Нажатие кнопки: обработчику нужны только callback и chat."""
    query = SimpleNamespace(
        data=callback_data,
        from_user=SimpleNamespace(id=1001, username='client'),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    return SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=1001))


@override_settings(MAX_KEYS=3)
class AddKeyChargeTests(TransactionTestCase):
    """Кнопка «Добавить» списывала сутки за подписку, которая уже работает.

    `add_vpn_to_user` возвращает найденную подписку сервера, а сервер выбирался
    случайно из всех. При одном сервере это значило: каждое следующее нажатие
    списывает деньги и возвращает ту же подписку.
    """

    def setUp(self):
        tariff = TariffServer.objects.create(name='сутки', price=Decimal('7.00'))
        self.server = Server.objects.create(
            name='NL', ip_address='192.0.2.10', ssh_username='x', ssh_password='x',
            vpn_username='x', vpn_password='x', vpn_key='x', inbound_id=5, tariff=tariff,
        )
        self.user = TelegramUser.objects.create(telegram_id=1001, username='client')
        Transaction.objects.create(
            user=self.user, amount=Decimal('100.00'), status='SUCCESS',
            source=TransactionSourceChoices.MANUAL,
        )

    def _run(self):
        import asyncio
        redis_client = MagicMock()
        redis_client.get.return_value = None
        with patch('apps.telegram_bot.handlers.add_key.redis.from_url', return_value=redis_client), \
                patch('apps.telegram_bot.handlers.add_key.render_screen', new_callable=AsyncMock), \
                patch('apps.telegram_bot.handlers.add_key.build_keys_screen',
                      new_callable=AsyncMock, return_value=('screen', None)), \
                patch('apps.vpn.services.add_vpn_to_user.vpn_client_for') as client:
            client.return_value.enable_user = AsyncMock()
            client.return_value.get_key = AsyncMock(return_value='vless://key')
            asyncio.run(add_key(_update(), None))

    def test_the_first_press_creates_a_subscription_and_charges_one_day(self):
        self._run()

        self.assertEqual(UserVPN.objects.count(), 1)
        self.assertEqual(Transaction.objects.filter(source=TransactionSourceChoices.BUY).count(), 1)

    def test_a_second_press_on_an_active_subscription_charges_nothing(self):
        self._run()
        self._run()

        self.assertEqual(UserVPN.objects.count(), 1)
        self.assertEqual(Transaction.objects.filter(source=TransactionSourceChoices.BUY).count(), 1)

    def test_a_disabled_subscription_can_still_be_paid_back_into_service(self):
        """Отключённая за неуплату подписка сервер не занимает — иначе её нечем оживить."""
        self._run()
        UserVPN.objects.update(enabled=False)

        self._run()

        self.assertEqual(UserVPN.objects.count(), 1)
        self.assertTrue(UserVPN.objects.get().enabled)
        self.assertEqual(Transaction.objects.filter(source=TransactionSourceChoices.BUY).count(), 2)
