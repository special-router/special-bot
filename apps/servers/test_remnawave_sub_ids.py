from io import StringIO
from unittest.mock import AsyncMock, patch
from uuid import UUID

from django.core.management import call_command
from django.test import TestCase, TransactionTestCase, override_settings

from apps.servers.models import Server, TariffServer
from apps.servers.remnawave import RemnawaveError
from apps.servers.remnawave_client import RemnawaveVPNClient
from apps.telegram_bot.models import Broadcast, BroadcastDelivery
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN
from apps.vpn.services.subscription_delivery import get_user_access_url


_PANEL = dict(
    REMNAWAVE_ENABLED=True,
    REMNAWAVE_API_URL='https://panel.test',
    REMNAWAVE_API_TOKEN='t' * 32,
    SUBSCRIPTION_BASE_URL='https://sub.example.test/sub',
    SUBSCRIPTION_DELIVERY_ENABLED=True,
)


@override_settings(**_PANEL)
class RemnawaveSubIdLifecycleTests(TransactionTestCase):
    def setUp(self):
        tariff = TariffServer.objects.create(name='test', price='7.00')
        self.server = Server.objects.create(
            name='NL', ip_address='192.0.2.1', ssh_username='unused', ssh_password='unused',
            vpn_username='unused', vpn_password='unused', vpn_key='unused', vpn_url='',
            client_vpn_host='vpn.example.test:443', tariff=tariff, inbound_id=5,
        )
        self.user = TelegramUser.objects.create(telegram_id=1001, username='client')
        self.connection = UserVPN.objects.create(
            user=self.user,
            server=self.server,
            vpn_uuid=UUID('11111111-2222-3333-4444-555555555555'),
            enabled=False,
            sub_id='',
        )

    def panel_user(self, short_uuid):
        return {
            'id': 9,
            'username': f'tg_{self.user.telegram_id}_{self.connection.id}',
            'vlessUuid': str(self.connection.vpn_uuid),
            'telegramId': self.user.telegram_id,
            'shortUuid': short_uuid,
            'status': 'ACTIVE',
        }

    @patch('apps.servers.remnawave_client.token_hex', return_value='a' * 32)
    def test_new_client_persists_id_before_create_and_delivers_https(self, _token_hex):
        client = RemnawaveVPNClient(self.server)
        calls = []

        async def find(_user_vpn):
            return None

        async def create_user(**kwargs):
            current_sub_id = await UserVPN.objects.values_list('sub_id', flat=True).aget(pk=self.connection.pk)
            calls.append((kwargs['short_uuid'], current_sub_id))
            return self.panel_user(kwargs['short_uuid'])

        client._find = find
        client._api.create_user = create_user

        import asyncio
        asyncio.run(client.enable_user(self.connection, enabled=True))
        self.connection.refresh_from_db()
        url = asyncio.run(get_user_access_url(self.connection))

        self.assertEqual(calls, [('a' * 32, 'a' * 32)])
        self.assertEqual(self.connection.sub_id, 'a' * 32)
        self.assertEqual(url, 'https://sub.example.test/sub/' + 'a' * 32)

    def test_reactivation_copies_existing_panel_id_before_enabling(self):
        client = RemnawaveVPNClient(self.server)
        panel = self.panel_user('b' * 32)
        client._find = AsyncMock(return_value=panel)
        client._api.set_status = AsyncMock()

        import asyncio
        asyncio.run(client.enable_user(self.connection, enabled=True))
        self.connection.refresh_from_db()

        self.assertEqual(self.connection.sub_id, 'b' * 32)
        client._api.set_status.assert_awaited_once_with(panel['id'], enabled=True)

    def test_reactivation_refuses_mismatched_panel_identity(self):
        client = RemnawaveVPNClient(self.server)
        panel = self.panel_user('b' * 32)
        panel['vlessUuid'] = '99999999-8888-7777-6666-555555555555'
        client._find = AsyncMock(return_value=panel)
        client._api.set_status = AsyncMock()

        import asyncio
        with self.assertRaises(RemnawaveError):
            asyncio.run(client.enable_user(self.connection, enabled=True))

        self.connection.refresh_from_db()
        self.assertEqual(self.connection.sub_id, '')
        client._api.set_status.assert_not_awaited()


@override_settings(**_PANEL)
class RepairRemnawaveSubIdsCommandTests(TestCase):
    def setUp(self):
        tariff = TariffServer.objects.create(name='test', price='7.00')
        self.server = Server.objects.create(
            name='NL', ip_address='192.0.2.1', ssh_username='unused', ssh_password='unused',
            vpn_username='unused', vpn_password='unused', vpn_key='unused', vpn_url='',
            client_vpn_host='vpn.example.test:443', tariff=tariff, inbound_id=5,
        )
        self.user = TelegramUser.objects.create(telegram_id=1001, username='client')
        self.connection = UserVPN.objects.create(
            user=self.user,
            server=self.server,
            vpn_uuid=UUID('11111111-2222-3333-4444-555555555555'),
            enabled=False,
            sub_id='',
        )

    def panel_user(self, short_uuid):
        return {
            'id': 9,
            'username': f'tg_{self.user.telegram_id}_{self.connection.id}',
            'vlessUuid': str(self.connection.vpn_uuid),
            'telegramId': self.user.telegram_id,
            'shortUuid': short_uuid,
            'status': 'ACTIVE',
        }
    @patch('apps.servers.management.commands.repair_remnawave_sub_ids.RemnawaveAPI')
    def test_dry_run_writes_nothing_and_prints_only_counts(self, api_class):
        api_class.return_value.get_user_by_username = AsyncMock(return_value=self.panel_user('c' * 32))
        output = StringIO()

        call_command('repair_remnawave_sub_ids', stdout=output)

        self.connection.refresh_from_db()
        self.assertEqual(self.connection.sub_id, '')
        self.assertIn('mode=dry-run candidates=1 panel_found=1', output.getvalue())
        self.assertNotIn('c' * 32, output.getvalue())
        self.assertNotIn(str(self.connection.vpn_uuid), output.getvalue())

    @patch('apps.telegram_bot.tasks.safe_broadcast_v1.delay')
    @patch('apps.servers.management.commands.repair_remnawave_sub_ids.RemnawaveAPI')
    def test_apply_repairs_and_queues_only_repaired_user(self, api_class, delay):
        other = TelegramUser.objects.create(telegram_id=1002, username='other')
        api_class.return_value.get_user_by_username = AsyncMock(return_value=self.panel_user('d' * 32))
        output = StringIO()

        with self.captureOnCommitCallbacks(execute=True):
            call_command('repair_remnawave_sub_ids', apply=True, notify=True, stdout=output)

        self.connection.refresh_from_db()
        self.assertEqual(self.connection.sub_id, 'd' * 32)
        broadcast = Broadcast.objects.get()
        self.assertTrue(broadcast.include_subscription_button)
        self.assertNotIn('http', broadcast.message.lower())
        self.assertEqual(
            list(BroadcastDelivery.objects.filter(broadcast=broadcast).values_list('user_id', flat=True)),
            [self.user.id],
        )
        self.assertNotIn(other.id, BroadcastDelivery.objects.values_list('user_id', flat=True))
        delay.assert_called_once_with(broadcast.id)
        self.assertIn('repaired=1 notification_queued=True', output.getvalue())

    @patch('apps.servers.management.commands.repair_remnawave_sub_ids.RemnawaveAPI')
    def test_identity_error_aborts_all_writes(self, api_class):
        second_user = TelegramUser.objects.create(telegram_id=1002, username='second')
        second = UserVPN.objects.create(user=second_user, server=self.server, enabled=True, sub_id='')

        async def get_user(username):
            if username.endswith(f'_{self.connection.id}'):
                return self.panel_user('e' * 32)
            return {
                'id': 10,
                'vlessUuid': str(second.vpn_uuid),
                'telegramId': 999999,
                'shortUuid': 'f' * 32,
            }

        api_class.return_value.get_user_by_username = get_user

        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            call_command('repair_remnawave_sub_ids', apply=True, stdout=StringIO())

        self.connection.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(self.connection.sub_id, '')
        self.assertEqual(second.sub_id, '')
