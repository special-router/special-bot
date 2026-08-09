from io import StringIO
from unittest.mock import AsyncMock, patch

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.payments.models import Transaction
from apps.servers.models import Server, TariffServer
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


@override_settings(SUBSCRIPTION_CONNECTOR_ENABLED=True, SUBSCRIPTION_BASE_URL='https://sub.example.test/sub')
class PrepareXuiSubscriptionsTests(TestCase):
    def setUp(self):
        tariff = TariffServer.objects.create(name='test', price='7.00')
        self.server = Server.objects.create(
            name='test',
            ip_address='127.0.0.1',
            ssh_username='unused',
            ssh_password='unused',
            vpn_username='unused',
            vpn_password='unused',
            vpn_key='unused',
            vpn_url='https://panel.invalid',
            client_vpn_host='127.0.0.1',
            tariff=tariff,
            inbound_id=5,
        )
        self.user = TelegramUser.objects.create(telegram_id=1001, username='test-user')
        self.connection = UserVPN.objects.create(user=self.user, server=self.server, enabled=True)
        self.other_connection = UserVPN.objects.create(user=self.user, server=self.server, enabled=True)

    @patch('apps.servers.management.commands.prepare_xui_subscriptions.XUISubscriptionConnector')
    def test_explicit_dry_run_counts_only_selected_record(self, connector_class):
        output = StringIO()

        call_command(
            'prepare_xui_subscriptions',
            server_id=self.server.id,
            user_vpn_id=self.connection.id,
            stdout=output,
        )

        self.assertIn('candidates=1 mode=dry-run changes=0', output.getvalue())
        connector_class.assert_not_called()

    @patch('apps.servers.management.commands.prepare_xui_subscriptions.XUISubscriptionConnector')
    def test_apply_refuses_record_without_balance_entitlement(self, connector_class):
        with self.assertRaisesMessage(CommandError, 'explicit record is not balance-entitled'):
            call_command(
                'prepare_xui_subscriptions',
                apply=True,
                server_id=self.server.id,
                user_vpn_id=self.connection.id,
                stdout=StringIO(),
            )

        connector_class.assert_not_called()

    @patch('apps.servers.management.commands.prepare_xui_subscriptions.XUISubscriptionConnector')
    def test_apply_prepares_one_explicit_entitled_record(self, connector_class):
        Transaction.objects.create(
            user=self.user,
            amount='14.00',
            status=TransactionStatusChoices.SUCCESS,
            source=TransactionSourceChoices.PROMO,
        )
        connector_class.return_value.ensure_subscription_reference = AsyncMock()
        output = StringIO()

        call_command(
            'prepare_xui_subscriptions',
            apply=True,
            server_id=self.server.id,
            user_vpn_id=self.connection.id,
            stdout=output,
        )

        connector_class.return_value.ensure_subscription_reference.assert_awaited_once()
        self.assertIn('candidates=1 mode=apply prepared=1', output.getvalue())
        self.assertNotIn(str(self.connection.vpn_uuid), output.getvalue())
