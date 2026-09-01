from unittest.mock import AsyncMock, patch
from uuid import UUID

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.servers.models import Server, TariffServer
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN
from bot.logging_filters import _redact


_SETTINGS = {
    'ROUTER_PROVISIONING_API_TOKEN': 'service-secret',
    'ROUTER_PROVISIONING_PUBLIC_BASE_URL': 'https://router.example.test',
}


@override_settings(**_SETTINGS)
class RouterProvisioningViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        tariff = TariffServer.objects.create(name='router', price='7.00')
        cls.server = Server.objects.create(
            name='NL', ip_address='192.0.2.10', ssh_username='unused', ssh_password='unused',
            vpn_username='unused', vpn_password='unused', vpn_key='unused', vpn_url='',
            client_vpn_host='vpn.example.test:443', tariff=tariff, inbound_id=5,
        )
        cls.user = TelegramUser.objects.create(telegram_id=1001, username='router-client')
        cls.other = TelegramUser.objects.create(telegram_id=1002, username='other-client')
        cls.existing = UserVPN.objects.create(
            user=cls.user, server=cls.server, enabled=True,
            vpn_uuid=UUID('11111111-2222-3333-4444-555555555555'),
        )
        cls.other_vpn = UserVPN.objects.create(user=cls.other, server=cls.server, enabled=True)

    def post(self, data, token='service-secret'):
        return self.client.post(
            reverse('router-provision'),
            data=data,
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {token}',
        )

    def test_missing_or_wrong_service_token_is_401(self):
        missing = self.client.post(
            reverse('router-provision'), data={'telegram_id': self.user.telegram_id},
            content_type='application/json',
        )
        wrong = self.post({'telegram_id': self.user.telegram_id}, token='wrong')

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(wrong.status_code, 401)

    def test_existing_customer_is_idempotent_and_scoped(self):
        response = self.post({'telegram_id': self.user.telegram_id})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'vpn_uuid': str(self.existing.vpn_uuid),
            'config_url': f'https://router.example.test/api/v1/vpn/box/{self.existing.vpn_uuid}/config/',
            'created': False,
        })
        self.assertNotIn(str(self.other_vpn.vpn_uuid), response.content.decode())
        self.assertEqual(response['Cache-Control'], 'private, no-store')

    @patch('api.v1.vpn.views.router_provision.add_vpn_to_user', new_callable=AsyncMock)
    def test_new_customer_uses_authoritative_creation_service(self, add_vpn):
        new_user = TelegramUser.objects.create(telegram_id=1003, username='new-client')
        created = UserVPN(
            id=33, user=new_user, server=self.server, server_id=self.server.id,
            vpn_uuid=UUID('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'), enabled=True,
        )
        add_vpn.return_value = created

        response = self.post({'telegram_id': new_user.telegram_id, 'server_id': self.server.id})

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()['created'])
        add_vpn.assert_awaited_once_with(new_user, self.server)

    @patch('api.v1.vpn.views.router_provision.add_vpn_to_user', new_callable=AsyncMock)
    def test_disabled_customer_is_reactivated(self, add_vpn):
        self.existing.enabled = False
        self.existing.save(update_fields=['enabled', 'updated_at'])
        self.existing.enabled = True
        add_vpn.return_value = self.existing

        response = self.post({'telegram_id': self.user.telegram_id})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['created'])
        add_vpn.assert_awaited_once()

    def test_unknown_customer_and_server_are_404(self):
        unknown_user = self.post({'telegram_id': 999999})
        unknown_server = self.post({'telegram_id': self.other.telegram_id, 'server_id': 999999})

        self.assertEqual(unknown_user.status_code, 404)
        self.assertEqual(unknown_server.status_code, 404)

    def test_multiple_vpns_require_explicit_server(self):
        second_tariff = TariffServer.objects.create(name='second', price='8.00')
        second_server = Server.objects.create(
            name='RU', ip_address='192.0.2.11', ssh_username='unused', ssh_password='unused',
            vpn_username='unused', vpn_password='unused', vpn_key='unused', vpn_url='',
            client_vpn_host='ru.example.test:443', tariff=second_tariff, inbound_id=6,
        )
        UserVPN.objects.create(user=self.user, server=second_server, enabled=True)

        ambiguous = self.post({'telegram_id': self.user.telegram_id})
        explicit = self.post({'telegram_id': self.user.telegram_id, 'server_id': self.server.id})

        self.assertEqual(ambiguous.status_code, 409)
        self.assertEqual(explicit.status_code, 200)
        self.assertEqual(explicit.json()['vpn_uuid'], str(self.existing.vpn_uuid))

    @patch('api.v1.vpn.views.router_provision.add_vpn_to_user', new_callable=AsyncMock)
    def test_backend_failure_is_secret_safe(self, add_vpn):
        new_user = TelegramUser.objects.create(telegram_id=1004, username='failing-client')
        add_vpn.side_effect = RuntimeError('panel secret detail')

        response = self.post({'telegram_id': new_user.telegram_id})

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'detail': 'Provisioning backend unavailable.'})
        self.assertNotIn('panel secret detail', response.content.decode())

    def test_authorization_bearer_is_redacted(self):
        value = _redact('Authorization: Bearer service-secret')
        self.assertEqual(value, 'Authorization: Bearer [REDACTED]')
        self.assertNotIn('service-secret', value)
