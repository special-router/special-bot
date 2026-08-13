from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from apps.servers.models import Server, TariffServer
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


def inbound(inbound_id, clients=(), stats=()):
    return SimpleNamespace(
        id=inbound_id,
        settings=SimpleNamespace(clients=list(clients)),
        client_stats=list(stats),
    )


def panel_client(client_id, email=''):
    return SimpleNamespace(id=str(client_id), email=email, inbound_id=None)


@override_settings(CLIENT_TRAFFIC_LABELS_ENABLED=True)
class BackfillClientLabelsTests(TestCase):
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
        self.label = f'uv-5-{self.connection.id}'

    def run_command(self, inbounds, **options):
        api = AsyncMock()
        api.inbound.get_list = AsyncMock(return_value=inbounds)
        output = StringIO()
        with patch('apps.servers.management.commands.backfill_client_labels.AsyncApi', return_value=api):
            call_command(
                'backfill_client_labels',
                server_id=self.server.id,
                inbound_id=5,
                stdout=output,
                **options,
            )
        return api, output.getvalue()

    def test_dry_run_plans_the_write_without_making_it(self):
        client = panel_client(self.connection.vpn_uuid)

        api, output = self.run_command([inbound(5, [client])])

        self.assertIn('mode=dry-run', output)
        self.assertIn('planned=1 changed=0', output)
        self.assertEqual(client.email, '')
        api.client.update.assert_not_awaited()

    def test_apply_labels_the_client_it_can_match(self):
        client = panel_client(self.connection.vpn_uuid)

        api, output = self.run_command([inbound(5, [client])], apply=True)

        self.assertIn('mode=apply', output)
        self.assertIn('planned=1 changed=1', output)
        self.assertEqual(client.email, self.label)
        self.assertEqual(client.inbound_id, 5)
        api.client.update.assert_awaited_once()

    def test_a_second_run_changes_nothing(self):
        client = panel_client(self.connection.vpn_uuid)
        self.run_command([inbound(5, [client])], apply=True)

        api, output = self.run_command([inbound(5, [client], [panel_client('', self.label)])], apply=True)

        self.assertIn('planned=0 changed=0 already_labelled=1', output)
        api.client.update.assert_not_awaited()

    def test_a_client_with_no_connection_is_never_touched(self):
        stranger = panel_client('11111111-2222-3333-4444-555555555555')

        api, output = self.run_command([inbound(5, [stranger])], apply=True)

        self.assertIn('planned=0 changed=0', output)
        self.assertIn('skipped_ownerless=1', output)
        self.assertEqual(stranger.email, '')
        api.client.update.assert_not_awaited()

    def test_refuses_a_label_another_client_already_carries(self):
        client = panel_client(self.connection.vpn_uuid)
        elsewhere = inbound(9, [panel_client('99999999-2222-3333-4444-555555555555', self.label)])

        with self.assertRaisesMessage(CommandError, 'label collisions; nothing was written'):
            self.run_command([inbound(5, [client]), elsewhere], apply=True)

        self.assertEqual(client.email, '')

    def test_refuses_a_label_an_orphaned_traffic_row_still_holds(self):
        client = panel_client(self.connection.vpn_uuid)
        orphan = inbound(5, [client], [panel_client('', self.label)])

        with self.assertRaisesMessage(CommandError, 'label collisions; nothing was written'):
            self.run_command([orphan], apply=True)

        self.assertEqual(client.email, '')

    def test_dry_run_reports_a_collision_instead_of_raising(self):
        client = panel_client(self.connection.vpn_uuid)
        elsewhere = inbound(9, [panel_client('99999999-2222-3333-4444-555555555555', self.label)])

        _, output = self.run_command([inbound(5, [client]), elsewhere])

        self.assertIn(f'collision label={self.label} held_by=inbound=9 client=99999999', output)
        self.assertIn('collisions=1', output)

    def test_leaves_a_label_it_did_not_write(self):
        client = panel_client(self.connection.vpn_uuid, email='осталось 28 дней')

        api, output = self.run_command([inbound(5, [client])], apply=True)

        self.assertIn('skipped_foreign_label=1', output)
        self.assertEqual(client.email, 'осталось 28 дней')
        api.client.update.assert_not_awaited()

    @override_settings(CLIENT_TRAFFIC_LABELS_ENABLED=False)
    def test_refuses_to_apply_while_labelling_is_disabled(self):
        """A label written now would be dropped by the next transport write."""
        client = panel_client(self.connection.vpn_uuid)

        with self.assertRaisesMessage(CommandError, 'CLIENT_TRAFFIC_LABELS_ENABLED is false'):
            self.run_command([inbound(5, [client])], apply=True)

        self.assertEqual(client.email, '')

    @override_settings(CLIENT_TRAFFIC_LABELS_ENABLED=False)
    def test_dry_run_still_works_while_labelling_is_disabled(self):
        api, output = self.run_command([inbound(5, [panel_client(self.connection.vpn_uuid)])])

        self.assertIn('mode=dry-run', output)
        self.assertIn('planned=1 changed=0', output)
        api.client.update.assert_not_awaited()

    def test_refuses_an_inbound_the_panel_does_not_have(self):
        with self.assertRaisesMessage(CommandError, 'is not present on this panel'):
            self.run_command([inbound(9)])

    def test_refuses_an_inbound_the_server_does_not_configure(self):
        """Inbounds 10 and 13 host a foreign tenant; the command cannot reach them."""
        api = AsyncMock()
        api.inbound.get_list = AsyncMock(return_value=[inbound(13, [panel_client(self.connection.vpn_uuid)])])
        with patch('apps.servers.management.commands.backfill_client_labels.AsyncApi', return_value=api):
            with self.assertRaisesMessage(CommandError, 'is not the primary inbound of server'):
                call_command('backfill_client_labels', server_id=self.server.id, inbound_id=13, stdout=StringIO())

        api.inbound.get_list.assert_not_awaited()

    def test_a_client_owned_by_another_servers_inbound_is_never_touched(self):
        other = Server.objects.get(id=self.server.id)
        other.pk = None
        other.inbound_id = 9
        other.save()
        stranger = UserVPN.objects.create(user=self.user, server=other, enabled=True)
        client = panel_client(stranger.vpn_uuid)

        api, output = self.run_command([inbound(5, [client])], apply=True)

        self.assertIn('skipped_ownerless=1', output)
        self.assertEqual(client.email, '')
        api.client.update.assert_not_awaited()

    def test_never_prints_a_full_client_uuid(self):
        client = panel_client(self.connection.vpn_uuid)

        _, output = self.run_command([inbound(5, [client])], list=True)

        self.assertIn(f'client={str(self.connection.vpn_uuid)[:8]} label={self.label}', output)
        self.assertNotIn(str(self.connection.vpn_uuid), output)
