from io import StringIO
from unittest.mock import AsyncMock, patch

from django.core.management import CommandError, call_command
from django.test import TestCase

from apps.servers.models import Server, TariffServer


class SubIdCoverageAuditCommandTests(TestCase):
    def setUp(self):
        tariff = TariffServer.objects.create(name='test', price='1.00')
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

    @patch('apps.servers.management.commands.audit_xui_subscription.AsyncApi')
    def test_reports_aggregate_counts_without_identifiers(self, api_class):
        clients = [
            type('Client', (), {'sub_id': 'secret-value', 'enable': True})(),
            type('Client', (), {'sub_id': '', 'enable': False})(),
        ]
        api = api_class.return_value
        api.login = AsyncMock()
        api.inbound.get_by_id = AsyncMock(
            return_value=type(
                'Inbound',
                (),
                {'settings': type('Settings', (), {'clients': clients})()},
            )()
        )
        output = StringIO()

        call_command('audit_xui_sub_id_coverage', server_id=self.server.id, stdout=output)

        text = output.getvalue()
        self.assertIn('clients=2', text)
        self.assertIn('enabled=1', text)
        self.assertIn('with_sub_id=1', text)
        self.assertIn('missing_sub_id=1', text)
        self.assertNotIn('secret-value', text)
        api.client.update.assert_not_called()

    def test_fails_closed_for_unknown_server(self):
        with self.assertRaisesMessage(CommandError, 'No matching configured servers.'):
            call_command('audit_xui_sub_id_coverage', server_id=999999, stdout=StringIO())
