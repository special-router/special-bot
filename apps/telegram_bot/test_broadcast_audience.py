from decimal import Decimal

from django.test import TestCase

from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.payments.models import Transaction
from apps.servers.models import Server, TariffServer
from apps.telegram_bot.models import Broadcast
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


class WithoutSubscriptionAudienceTests(TestCase):
    """Две рассылки на разные половины аудитории не должны пересекаться."""

    def setUp(self):
        tariff = TariffServer.objects.create(name='сутки', price=Decimal('7.00'))
        self.server = Server.objects.create(
            name='NL', ip_address='192.0.2.10', ssh_username='x', ssh_password='x',
            vpn_username='x', vpn_password='x', vpn_key='x', inbound_id=5, tariff=tariff,
        )

    def _user(self, telegram_id, balance='0.00'):
        user = TelegramUser.objects.create(telegram_id=telegram_id, username=f'u{telegram_id}')
        Transaction.objects.create(
            user=user, amount=Decimal(balance), status=TransactionStatusChoices.SUCCESS,
            source=TransactionSourceChoices.MANUAL,
        )
        return user

    def _ids(self, audience):
        return set(Broadcast(audience=audience).recipient_queryset().values_list('pk', flat=True))

    def test_the_two_halves_partition_everyone_exactly_once(self):
        client = self._user(1001, '100.00')
        UserVPN.objects.create(user=client, server=self.server, sub_id='ready-sub-id')
        stranger = self._user(1002)

        ready = self._ids(Broadcast.AUDIENCE_SUBSCRIPTION_READY)
        rest = self._ids(Broadcast.AUDIENCE_WITHOUT_SUBSCRIPTION)
        everyone = self._ids(Broadcast.AUDIENCE_ALL)

        self.assertEqual(ready, {client.pk})
        self.assertEqual(rest, {stranger.pk})
        self.assertEqual(ready | rest, everyone)
        self.assertEqual(ready & rest, set())

    def test_a_subscription_the_balance_cannot_afford_lands_in_the_second_half(self):
        """Иначе он получил бы письмо «обновите подписку», которой у него нет."""
        broke = self._user(1003, '1.00')
        UserVPN.objects.create(user=broke, server=self.server, sub_id='unaffordable')

        self.assertEqual(self._ids(Broadcast.AUDIENCE_SUBSCRIPTION_READY), set())
        self.assertEqual(self._ids(Broadcast.AUDIENCE_WITHOUT_SUBSCRIPTION), {broke.pk})
