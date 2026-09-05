import base64
import json
from unittest.mock import patch

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
