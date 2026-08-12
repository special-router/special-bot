from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.servers.models import Server, TariffServer


class ValidateInboundConfigTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        tariff = TariffServer.objects.create(name='base', price=7)
        cls.server = Server.objects.create(
            name='NL', vpn_url='http://panel.invalid:23133', vpn_username='u', vpn_password='p',
            vpn_key='', client_vpn_host='vpn.invalid:443', inbound_id=5, tariff=tariff,
            ip_address='203.0.113.10', ssh_username='root', ssh_password='x',
        )

    def _api(self, inbound_ids):
        api = AsyncMock()
        api.login = AsyncMock()
        api.inbound.get_list = AsyncMock(
            return_value=[SimpleNamespace(id=value) for value in inbound_ids])
        return api

    @override_settings(SPECIAL_MONITOR_SERVER_ID=1, MIRROR_INBOUND_IDS=[14], STATUS_INBOUND_ID=1)
    def test_strict_run_fails_when_configured_ids_are_absent(self):
        with patch('apps.servers.management.commands.validate_inbound_config.AsyncApi',
                   return_value=self._api([5, 7, 13])):
            with self.assertRaisesRegex(CommandError, 'mirror:14'):
                call_command('validate_inbound_config', '--strict')

    @override_settings(SPECIAL_MONITOR_SERVER_ID=1, MIRROR_INBOUND_IDS=[], STATUS_INBOUND_ID=0)
    def test_clean_config_passes_and_checks_primary_only(self):
        with patch('apps.servers.management.commands.validate_inbound_config.AsyncApi',
                   return_value=self._api([5, 7, 13])):
            call_command('validate_inbound_config', '--strict')

    @override_settings(SPECIAL_MONITOR_SERVER_ID=1, MIRROR_INBOUND_IDS=[14], STATUS_INBOUND_ID=1)
    def test_non_strict_run_reports_without_failing(self):
        with patch('apps.servers.management.commands.validate_inbound_config.AsyncApi',
                   return_value=self._api([5])):
            call_command('validate_inbound_config')
