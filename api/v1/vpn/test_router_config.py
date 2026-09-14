from tempfile import TemporaryDirectory

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.servers.models import Server, TariffServer
from apps.subscriptions.provider_snapshots import _publish, _publish_scope
from apps.subscriptions.tokens import issue_router_activation_code
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


LINE = ('vless://11111111-2222-3333-4444-555555555555@192.0.2.10:443?'
        'type=tcp&security=reality&sni=example.test&pbk=public-key&sid=abcdef01#Germany')
MANIFEST = [{'id': 'a-service', 'adapter': 'subscription', 'host': 'a.example', 'enabled': True}]


class RouterDeviceTokenTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        tariff = TariffServer.objects.create(name='router-token', price='7.00')
        server = Server.objects.create(
            name='NL', ip_address='192.0.2.10', ssh_username='unused', ssh_password='unused',
            vpn_username='unused', vpn_password='unused', vpn_key='unused', vpn_url='',
            client_vpn_host='vpn.example.test:443', tariff=tariff, inbound_id=5,
        )
        user = TelegramUser.objects.create(telegram_id=1009, username='router-token')
        cls.subscription = UserVPN.objects.create(user=user, server=server, enabled=True)

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings = override_settings(
            SUBSCRIPTION_PROVIDER_SNAPSHOTS_ENABLED=True,
            SUBSCRIPTION_PROVIDER_SNAPSHOT_DIR=self.directory.name,
            SUBSCRIPTION_PROVIDER_SNAPSHOT_MAX_AGE_SECONDS=86400,
            SUBSCRIPTION_BACKUP_PROVIDER_MANIFEST=MANIFEST,
            ROUTER_PROVIDER_SNAPSHOT_ENABLED=True,
        )
        self.settings.enable()
        self.addCleanup(self.settings.disable)
        _publish('a-service', [LINE])
        _publish_scope(('a-service',))

    def test_activation_is_single_use_and_device_token_fetches_config(self):
        code, _record = issue_router_activation_code(self.subscription)
        first = self.client.post(
            reverse('router-activate'), {'code': code}, content_type='application/json')
        second = self.client.post(
            reverse('router-activate'), {'code': code}, content_type='application/json')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 404)
        self.assertNotIn(str(self.subscription.vpn_uuid), first.content.decode())
        token = first.json()['device_token']
        config = self.client.get(reverse('router-config'), HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(config.status_code, 200)
        self.assertEqual(config.json()['outbounds'][0]['tag'], 'GLOBAL AUTO')

    def test_missing_or_invalid_device_token_is_indistinguishable(self):
        missing = self.client.get(reverse('router-config'))
        invalid = self.client.get(reverse('router-config'), HTTP_AUTHORIZATION='Bearer invalid')
        self.assertEqual((missing.status_code, missing.content), (invalid.status_code, invalid.content))

    def test_large_config_response_is_gzipped_and_correlated(self):
        many = [LINE.replace('#Germany', f'#Germany-{index}') for index in range(20)]
        _publish('a-service', many)
        code, _record = issue_router_activation_code(self.subscription)
        activated = self.client.post(reverse('router-activate'), {'code': code}, content_type='application/json')
        response = self.client.get(
            reverse('router-config'), HTTP_AUTHORIZATION=f"Bearer {activated.json()['device_token']}",
            HTTP_ACCEPT_ENCODING='gzip', HTTP_X_REQUEST_ID='request-correlation-1234')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Encoding'], 'gzip')
        self.assertEqual(response['X-Request-ID'], 'request-correlation-1234')
