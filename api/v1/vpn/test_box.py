from unittest.mock import AsyncMock, patch
from uuid import UUID

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.servers.models import Server, TariffServer
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN
from bot.logging_filters import _redact


class VPNBoxConfigViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        tariff = TariffServer.objects.create(name='router', price='7.00')
        cls.server = Server.objects.create(
            name='NL',
            ip_address='192.0.2.10',
            ssh_username='unused',
            ssh_password='unused',
            vpn_username='unused',
            vpn_password='unused',
            vpn_key='unused',
            vpn_url='',
            client_vpn_host='vpn.example.test:443',
            tariff=tariff,
            inbound_id=5,
        )
        user = TelegramUser.objects.create(telegram_id=1001, username='router')
        cls.enabled = UserVPN.objects.create(
            user=user,
            server=cls.server,
            vpn_uuid=UUID('11111111-2222-3333-4444-555555555555'),
            enabled=True,
        )
        cls.disabled = UserVPN.objects.create(
            user=user,
            server=cls.server,
            vpn_uuid=UUID('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'),
            enabled=False,
        )

    @staticmethod
    def _config():
        return {
            'outbounds': [{
                'tag': 'proxy',
                'protocol': 'vless',
                'settings': {'vnext': []},
            }],
        }

    @patch('api.v1.vpn.views.box.vpn_client_for')
    def test_enabled_uuid_returns_router_config_without_cache(self, client_for):
        client_for.return_value.get_raw_inbound_config = AsyncMock(return_value=self._config())

        response = self.client.get(reverse('vpn-box-config', kwargs={'vpn_uuid': self.enabled.vpn_uuid}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self._config())
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response['Pragma'], 'no-cache')
        client_for.assert_called_once_with(self.server)

    @patch('api.v1.vpn.views.box.vpn_client_for')
    def test_disabled_and_unknown_uuid_are_indistinguishable(self, client_for):
        disabled = self.client.get(reverse('vpn-box-config', kwargs={'vpn_uuid': self.disabled.vpn_uuid}))
        unknown = self.client.get(reverse(
            'vpn-box-config',
            kwargs={'vpn_uuid': UUID('99999999-8888-7777-6666-555555555555')},
        ))

        self.assertEqual(disabled.status_code, 404)
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(disabled.content, unknown.content)
        client_for.assert_not_called()

        self.assertEqual(disabled['Cache-Control'], 'private, no-store')
        self.assertEqual(unknown['Cache-Control'], 'private, no-store')

    def test_uuid_router_path_is_redacted_from_logs(self):
        path = reverse('vpn-box-config', kwargs={'vpn_uuid': self.enabled.vpn_uuid})

        redacted = _redact(f'Not Found: {path}')

        self.assertEqual(redacted, 'Not Found: /api/v1/vpn/box/[REDACTED]/config/')
        self.assertNotIn(str(self.enabled.vpn_uuid), redacted)


class RemnawaveRouterConfigTests(TestCase):
    @patch('apps.servers.remnawave_client.RemnawaveAPI')
    @override_settings(
        REMNAWAVE_REALITY_PUBLIC_KEY='synthetic-public-key',
        REMNAWAVE_REALITY_SERVER_NAME='example.test',
        REMNAWAVE_REALITY_SHORT_ID='abcdef01',
        REMNAWAVE_REALITY_FINGERPRINT='chrome',
        REMNAWAVE_REALITY_PORT=443,
    )
    def test_router_config_matches_production_no_flow_clients(self, _api):
        from apps.servers.remnawave_client import RemnawaveVPNClient

        server = type('ServerValue', (), {'client_vpn_host': 'vpn.example.test:443'})()
        user_vpn = type('UserVPNValue', (), {'vpn_uuid': 'synthetic-client-id'})()

        import asyncio
        config = asyncio.run(RemnawaveVPNClient(server).get_raw_inbound_config(user_vpn))
        user = config['outbounds'][0]['settings']['vnext'][0]['users'][0]

        self.assertEqual(user['id'], 'synthetic-client-id')
        self.assertNotIn('flow', user)
