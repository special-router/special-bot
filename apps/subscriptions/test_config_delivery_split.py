import base64
import json
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

import httpx
from django.test import RequestFactory, TestCase, override_settings

from apps.servers.models import Server, TariffServer
from apps.subscriptions.views import subscription_proxy
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN

_UUID = '11111111-2222-3333-4444-555555555555'
_DIRECT = (f'vless://{_UUID}@sub.special-wifi.ru:443?encryption=none&security=reality'
           '&sni=example.test&fp=chrome&pbk=key&sid=aabb&type=tcp#direct')
_RELAY = (f'vless://{_UUID}@201.34.132.118:443?encryption=none&security=reality'
          '&sni=example.test&fp=chrome&pbk=key&sid=aabb&type=tcp#relay')
_XHTTP = (f'vless://{_UUID}@sub.special-wifi.ru:443?encryption=none&security=tls'
          '&sni=sub.special-wifi.ru&fp=chrome&type=xhttp&path=%2Fassets#xhttp')
_GRPC = (f'vless://{_UUID}@sub.special-wifi.ru:80?encryption=none&security=reality'
         '&sni=example.test&fp=chrome&pbk=key&sid=aabb&type=grpc&serviceName=google#grpc')


@override_settings(
    SUBSCRIPTION_BASE_URL='https://cfg.special-wifi.ru/sub/',
    REMNAWAVE_ENDPOINTS_ENABLED=True,
    REMNAWAVE_ENDPOINTS_ALL_USERS_ENABLED=True,
    REMNAWAVE_API_URL='https://panel.test',
    SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=False,
    SUBSCRIPTION_STATUS_ENTRY_ENABLED=False,
    SUBSCRIPTION_XRAY_JSON_ENABLED=True,
    SUBSCRIPTION_XRAY_JSON_ROLLED_OUT_CLIENTS=['happ'],
)
class ConfigDeliverySplitTests(TestCase):
    def setUp(self):
        user = TelegramUser.objects.create(telegram_id=99)
        tariff = TariffServer.objects.create(name='test', price='1.00')
        server = Server.objects.create(
            name='NL', ip_address='192.0.2.1', ssh_username='x', ssh_password='x',
            vpn_username='x', vpn_password='x', vpn_key='x', tariff=tariff,
            vpn_url='https://sub.special-wifi.ru/panel-path',
            inbound_id=5, client_vpn_host='201.34.132.118:443',
        )
        self.vpn = UserVPN.objects.create(
            user=user, server=server, vpn_uuid=_UUID, vpn_key=_RELAY,
            sub_id='b' * 32, enabled=True,
        )

    @patch('apps.subscriptions.views._get_params', return_value={
        'port': 8443, 'public_key': 'key', 'server_name': 'example.test',
        'short_ids': ['aabb'], 'network': 'tcp', 'security': 'reality',
        'fingerprint': 'chrome', 'service_name': '', 'path': '', 'host': '',
    })
    @patch('apps.subscriptions.views.httpx.get')
    def test_json_uses_panel_vpn_hosts_not_the_config_delivery_hostname(self, get, _params):
        get.return_value = httpx.Response(200, content=base64.b64encode(
            ('\n'.join((_DIRECT, _RELAY, _XHTTP, _GRPC)) + '\n').encode()))
        request = RequestFactory().get('/sub/x', HTTP_USER_AGENT='Happ/1.0')
        request.user = None

        response = subscription_proxy(request, self.vpn.sub_id)
        documents = json.loads(response.content)
        addresses = [outbound['settings']['vnext'][0]['address']
                     for outbound in documents[0]['outbounds']
                     if outbound.get('protocol') == 'vless']

        self.assertIn('sub.special-wifi.ru', addresses)
        self.assertIn('201.34.132.118', addresses)
        self.assertNotIn('cfg.special-wifi.ru', addresses)


@override_settings(
    SUBSCRIPTION_BASE_URL='https://cfg.special-wifi.ru/sub/',
    REMNAWAVE_ENDPOINTS_ENABLED=True,
    REMNAWAVE_ENDPOINTS_ALL_USERS_ENABLED=True,
    REMNAWAVE_API_URL='https://panel.test',
    SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=False,
    SUBSCRIPTION_STATUS_ENTRY_ENABLED=False,
    SUBSCRIPTION_XRAY_JSON_ENABLED=False,
    SUBSCRIPTION_CANARY_RELAY_ENDPOINT={'host': '201.34.132.118', 'port': 443},
    SUBSCRIPTION_CANARY_RELAY_TEST_USER_IDS=[801],
)
class PerUserRelayDocumentTests(TestCase):
    def setUp(self):
        tariff = TariffServer.objects.create(name='test-canary', price='1.00')
        server = Server.objects.create(
            name='NL canary', ip_address='192.0.2.2', ssh_username='x', ssh_password='x',
            vpn_username='x', vpn_password='x', vpn_key='x', tariff=tariff,
            vpn_url='https://sub.special-wifi.ru/panel-path', inbound_id=5,
            client_vpn_host='',
        )
        self.canary = UserVPN.objects.create(
            id=801, user=TelegramUser.objects.create(telegram_id=801), server=server,
            vpn_uuid=_UUID, vpn_key=_DIRECT, sub_id='c' * 32, enabled=True,
        )
        self.ordinary = UserVPN.objects.create(
            id=802, user=TelegramUser.objects.create(telegram_id=802), server=server,
            vpn_uuid='99999999-2222-3333-4444-555555555555', vpn_key=_DIRECT,
            sub_id='d' * 32, enabled=True,
        )

    @patch('apps.subscriptions.views._get_params', return_value={
        'port': 8443, 'public_key': 'key', 'server_name': 'example.test',
        'short_ids': ['aabb'], 'network': 'tcp', 'security': 'reality',
        'fingerprint': 'chrome', 'service_name': '', 'path': '', 'host': '',
    })
    @patch('apps.subscriptions.views.httpx.get')
    def test_relay_is_added_only_to_canary_document(self, get, _params):
        def panel_response(url, **_kwargs):
            uuid = _UUID if self.canary.sub_id in url else str(self.ordinary.vpn_uuid)
            direct = _DIRECT.replace(_UUID, uuid)
            xhttp = _XHTTP.replace(_UUID, uuid)
            grpc = _GRPC.replace(_UUID, uuid)
            return httpx.Response(200, content=base64.b64encode(
                ('\n'.join((direct, xhttp, grpc)) + '\n').encode()),
                request=httpx.Request('GET', url))
        get.side_effect = panel_response

        canary = subscription_proxy(RequestFactory().get('/sub/canary'), self.canary.sub_id)
        ordinary = subscription_proxy(RequestFactory().get('/sub/ordinary'), self.ordinary.sub_id)
        canary_links = base64.b64decode(canary.content).decode().splitlines()
        ordinary_links = base64.b64decode(ordinary.content).decode().splitlines()

        self.assertEqual(sum(urlsplit(link).hostname == '201.34.132.118'
                             for link in canary_links), 1)
        self.assertTrue(any('белые списки' in unquote(urlsplit(link).fragment)
                            for link in canary_links))
        self.assertFalse(any(urlsplit(link).hostname == '201.34.132.118'
                             for link in ordinary_links))
        self.assertFalse(any('белые списки' in unquote(urlsplit(link).fragment)
                             for link in ordinary_links))
