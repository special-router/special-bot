from io import StringIO
from unittest.mock import AsyncMock, patch
from uuid import UUID

from django.core import management
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase
from django.utils.timezone import now

from apps.payments.choices import TransactionStatusChoices
from apps.servers.models import Server, TariffServer
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


class LegacyVpnAuditCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tariff = TariffServer.objects.create(name='legacy', price='7.00')
        cls.server = Server.objects.create(
            name='NL',
            ip_address='192.0.2.10',
            ssh_username='audit-user',
            ssh_password='not-used',
            vpn_username='panel-user',
            vpn_password='not-used',
            vpn_key='not-used',
            vpn_url='https://panel.invalid/',
            client_vpn_host='relay.invalid:443',
            tariff=cls.tariff,
            inbound_id=5,
        )
        cls.user = TelegramUser.objects.create(telegram_id=1001, username='paid')
        cls.unpaid_user = TelegramUser.objects.create(telegram_id=1002, username='unpaid')
        cls.paid_uuid = UUID('00000000-0000-0000-0000-000000000001')
        cls.unpaid_uuid = UUID('00000000-0000-0000-0000-000000000002')
        UserVPN.objects.create(user=cls.user, server=cls.server, vpn_uuid=cls.paid_uuid)
        UserVPN.objects.create(user=cls.unpaid_user, server=cls.server, vpn_uuid=cls.unpaid_uuid)

        # The production-sync model contains fields not represented in its old migrations.
        # Insert only the legacy columns so the test remains faithful to that schema.
        with connection.cursor() as cursor:
            cursor.execute(
                'INSERT INTO payments_transaction '
                '(amount, status, created_at, source, invoice_id, user_id) '
                'VALUES (%s, %s, %s, %s, %s, %s)',
                ('7.00', TransactionStatusChoices.SUCCESS, now(), 'MANUAL', None, cls.user.id),
            )

    @patch('apps.vpn.management.commands.audit_legacy_vpn.fetch_control_plane_client_ids', new_callable=AsyncMock)
    def test_matching_entitlement_passes(self, fetch_clients):
        fetch_clients.return_value = ({str(self.paid_uuid)}, {str(self.paid_uuid)})
        output = StringIO()

        management.call_command('audit_legacy_vpn', stdout=output)

        text = output.getvalue()
        self.assertIn('records=2', text)
        self.assertIn('entitled=1', text)
        self.assertIn('control_plane=1', text)
        self.assertIn('control_plane_enabled=1', text)
        self.assertIn('entitled_missing=0', text)
        self.assertIn('extras=0', text)
        self.assertIn('compatibility_count=0', text)
        self.assertNotIn(str(self.paid_uuid), text)
        self.assertNotIn(str(self.unpaid_uuid), text)
        self.assertNotIn('1001', text)
        self.assertNotIn('1002', text)

    @patch('apps.vpn.management.commands.audit_legacy_vpn.fetch_control_plane_client_ids', new_callable=AsyncMock)
    def test_missing_entitled_client_fails(self, fetch_clients):
        fetch_clients.return_value = (set(), set())

        with self.assertRaises(CommandError) as raised:
            management.call_command('audit_legacy_vpn', stdout=StringIO(), stderr=StringIO())

        self.assertIn('entitled_missing=1', str(raised.exception))

    @patch('apps.vpn.management.commands.audit_legacy_vpn.fetch_control_plane_client_ids', new_callable=AsyncMock)
    def test_disabled_entitled_client_fails(self, fetch_clients):
        fetch_clients.return_value = ({str(self.paid_uuid)}, set())

        with self.assertRaises(CommandError) as raised:
            management.call_command('audit_legacy_vpn', stdout=StringIO(), stderr=StringIO())

        self.assertIn('entitled_missing=1', str(raised.exception))

    @patch('apps.vpn.management.commands.audit_legacy_vpn.fetch_control_plane_client_ids', new_callable=AsyncMock)
    def test_unpaid_client_is_not_required(self, fetch_clients):
        fetch_clients.return_value = (set(), set())

        with self.assertRaises(CommandError) as raised:
            management.call_command('audit_legacy_vpn', stdout=StringIO(), stderr=StringIO())

        self.assertIn('entitled_missing=1', str(raised.exception))
        self.assertNotIn('entitled_missing=2', str(raised.exception))

    @patch('apps.vpn.management.commands.audit_legacy_vpn.fetch_control_plane_client_ids', new_callable=AsyncMock)
    def test_control_plane_api_error_is_sanitized(self, fetch_clients):
        fetch_clients.side_effect = RuntimeError('https://user:password@panel.invalid/secret')
        output = StringIO()

        with self.assertRaises(CommandError) as raised:
            management.call_command('audit_legacy_vpn', stdout=output, stderr=StringIO())

        self.assertEqual(str(raised.exception), f'Legacy VPN audit failed for server_id={self.server.id}.')
        self.assertNotIn('panel.invalid', str(raised.exception))
        self.assertNotIn('password', str(raised.exception))

    @patch('apps.vpn.management.commands.audit_legacy_vpn.fetch_control_plane_client_ids', new_callable=AsyncMock)
    def test_control_plane_extra_is_reported_without_failing(self, fetch_clients):
        fetch_clients.return_value = (
            {str(self.paid_uuid), str(self.unpaid_uuid)},
            {str(self.paid_uuid), str(self.unpaid_uuid)},
        )
        output = StringIO()

        management.call_command('audit_legacy_vpn', stdout=output)

        text = output.getvalue()
        self.assertIn('entitled_missing=0', text)
        self.assertIn('extras=1', text)
        self.assertIn('compatibility_count=0', text)
        self.assertNotIn(str(self.unpaid_uuid), text)

    @patch('apps.vpn.management.commands.audit_legacy_vpn.fetch_control_plane_client_ids', new_callable=AsyncMock)
    def test_control_plane_only_identity_is_counted_without_exposure(self, fetch_clients):
        compatibility_uuid = '00000000-0000-0000-0000-000000000003'
        fetch_clients.return_value = (
            {str(self.paid_uuid), compatibility_uuid},
            {str(self.paid_uuid), compatibility_uuid},
        )
        output = StringIO()

        management.call_command('audit_legacy_vpn', stdout=output)

        text = output.getvalue()
        self.assertIn('entitled_missing=0', text)
        self.assertIn('extras=1', text)
        self.assertIn('compatibility_count=1', text)
        self.assertNotIn(compatibility_uuid, text)
