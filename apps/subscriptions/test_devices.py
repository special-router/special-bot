import base64
from datetime import timedelta
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from apps.servers.models import Server, TariffServer
from apps.subscriptions import views
from apps.subscriptions.devices import client_hwid, reset_devices
from apps.subscriptions.models import SubscriptionDevice, SubscriptionDeviceReset
from apps.telegram_bot.handlers.reset_devices import reset_devices as reset_devices_handler
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


DEVICE_A = 'aaaaaaaaaa11'
DEVICE_B = 'bbbbbbbbbb22'
DEVICE_C = 'cccccccccc33'

PARAMS = {
    'public_key': 'synthetic-public-key',
    'server_name': 'sni.example',
    'short_ids': ['synthetic-short-id'],
    'port': 8443,
    'network': 'tcp',
}


@override_settings(
    SUBSCRIPTION_BASE_URL='https://direct.example/sub',
    SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=False,
    SUBSCRIPTION_INTERNAL_INBOUNDS_ENABLED=False,
    SUBSCRIPTION_DEVICE_LIMIT=2,
    SUBSCRIPTION_HWID_STRICT=False,
)
@patch('apps.subscriptions.views._get_params', return_value=PARAMS)
class DeviceBindingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        tariff = TariffServer.objects.create(name='base', price='7.00')
        server = Server.objects.create(
            name='SPECIAL', ip_address='198.51.100.10', ssh_username='ssh', ssh_password='ssh',
            vpn_username='panel', vpn_password='panel', vpn_key='key',
            client_vpn_host='relay.example:443', tariff=tariff, inbound_id=5,
        )
        user = TelegramUser.objects.create(telegram_id=1001, username='holder')
        cls.user_vpn = UserVPN.objects.create(user=user, server=server, sub_id='synthetic')

    def _request(self, headers=None):
        return views.subscription_proxy(
            RequestFactory().get('/sub/synthetic', headers=headers or {}), 'synthetic')

    def test_first_device_is_registered_and_served(self, _params):
        response = self._request({'x-hwid': DEVICE_A})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['x-hwid-active'], 'true')
        self.assertNotIn('x-hwid-max-devices-reached', response)
        self.assertEqual(SubscriptionDevice.objects.filter(subscription=self.user_vpn).count(), 1)

    def test_second_device_is_served(self, _params):
        self._request({'x-hwid': DEVICE_A})

        response = self._request({'x-hwid': DEVICE_B})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(SubscriptionDevice.objects.filter(subscription=self.user_vpn).count(), 2)

    def test_third_device_is_refused_with_limit_headers(self, _params):
        self._request({'x-hwid': DEVICE_A})
        self._request({'x-hwid': DEVICE_B})

        response = self._request({'x-hwid': DEVICE_C})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response['x-hwid-active'], 'true')
        self.assertEqual(response['x-hwid-max-devices-reached'], 'true')
        self.assertEqual(response['x-hwid-limit'], 'true')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        # The refusal must not disclose the fleet, the subscription, or a count.
        self.assertEqual(response.content, b'')
        self.assertEqual(SubscriptionDevice.objects.filter(subscription=self.user_vpn).count(), 2)

    def test_known_device_is_still_served_at_the_limit(self, _params):
        self._request({'x-hwid': DEVICE_A})
        self._request({'x-hwid': DEVICE_B})
        registered = SubscriptionDevice.objects.get(subscription=self.user_vpn, hwid=DEVICE_A)
        SubscriptionDevice.objects.filter(pk=registered.pk).update(
            last_seen_at=timezone.now() - timedelta(hours=3))

        response = self._request({'x-hwid': DEVICE_A})

        self.assertEqual(response.status_code, 200)
        registered.refresh_from_db()
        self.assertGreater(registered.last_seen_at, timezone.now() - timedelta(minutes=1))

    def test_malformed_hwid_is_treated_as_absent(self, _params):
        response = self._request({'x-hwid': 'short'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['x-hwid-not-supported'], 'true')
        self.assertFalse(SubscriptionDevice.objects.filter(subscription=self.user_vpn).exists())

    def test_missing_hwid_is_served_by_default(self, _params):
        response = self._request()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['x-hwid-active'], 'true')
        self.assertEqual(response['x-hwid-not-supported'], 'true')
        self.assertFalse(SubscriptionDevice.objects.filter(subscription=self.user_vpn).exists())

    @override_settings(SUBSCRIPTION_HWID_STRICT=True)
    def test_strict_mode_refuses_a_request_without_hwid(self, _params):
        response = self._request()

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response['x-hwid-active'], 'true')
        self.assertEqual(response['x-hwid-not-supported'], 'true')
        self.assertEqual(response.content, b'')

    def test_served_device_keeps_the_legacy_response_contract(self, _params):
        response = self._request({'x-hwid': DEVICE_A})
        lines = base64.b64decode(response.content).decode().splitlines()

        self.assertEqual(len(lines), 3)
        self.assertEqual(response['Profile-Update-Interval'], '12')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response['Pragma'], 'no-cache')

    def test_per_subscription_override_raises_the_limit(self, _params):
        UserVPN.objects.filter(pk=self.user_vpn.pk).update(device_limit=3)
        self._request({'x-hwid': DEVICE_A})
        self._request({'x-hwid': DEVICE_B})

        response = self._request({'x-hwid': DEVICE_C})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(SubscriptionDevice.objects.filter(subscription=self.user_vpn).count(), 3)

    def test_device_metadata_is_stored_within_column_bounds(self, _params):
        self._request({
            'x-hwid': DEVICE_A,
            'x-device-os': 'A' * 200,
            'x-ver-os': 'B' * 200,
            'x-device-model': 'C' * 200,
            'user-agent': 'D' * 400,
        })

        device = SubscriptionDevice.objects.get(subscription=self.user_vpn, hwid=DEVICE_A)
        self.assertEqual(len(device.device_os), 32)
        self.assertEqual(len(device.os_version), 32)
        self.assertEqual(len(device.device_model), 64)
        self.assertEqual(len(device.user_agent), 128)

    def test_forged_hwid_flood_cannot_grow_the_table(self, _params):
        for index in range(20):
            self._request({'x-hwid': f'forged{index:06d}'})

        self.assertEqual(SubscriptionDevice.objects.filter(subscription=self.user_vpn).count(), 2)

    def test_disabled_subscription_is_refused_without_hwid_headers(self, _params):
        UserVPN.objects.filter(pk=self.user_vpn.pk).update(enabled=False)

        response = self._request({'x-hwid': DEVICE_A})

        self.assertEqual(response.status_code, 404)
        self.assertNotIn('x-hwid-active', response)


class ClientHwidTests(TestCase):
    def _hwid(self, value):
        return client_hwid(RequestFactory().get('/sub/synthetic', headers={'x-hwid': value}))

    def test_accepted_alphabet_and_length(self):
        self.assertEqual(self._hwid('Abc-123=xyz'), 'Abc-123=xyz')
        self.assertEqual(self._hwid('z' * 64), 'z' * 64)

    def test_rejected_shapes_are_reported_as_absent(self):
        for value in ('', 'nine_char', 'z' * 65, 'has space01', 'semi;colon01', 'слишкомдлинный'):
            with self.subTest(value=value):
                self.assertEqual(self._hwid(value), '')


class DeviceResetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        tariff = TariffServer.objects.create(name='base', price='7.00')
        server = Server.objects.create(
            name='SPECIAL', ip_address='198.51.100.10', ssh_username='ssh', ssh_password='ssh',
            vpn_username='panel', vpn_password='panel', vpn_key='key',
            client_vpn_host='relay.example:443', tariff=tariff, inbound_id=5,
        )
        cls.user = TelegramUser.objects.create(telegram_id=1001, username='holder')
        cls.other_user = TelegramUser.objects.create(telegram_id=1002, username='stranger')
        cls.user_vpn = UserVPN.objects.create(user=cls.user, server=server, sub_id='synthetic')
        cls.other_vpn = UserVPN.objects.create(user=cls.other_user, server=server, sub_id='other')

    def setUp(self):
        SubscriptionDevice.objects.create(subscription=self.user_vpn, hwid=DEVICE_A)
        SubscriptionDevice.objects.create(subscription=self.user_vpn, hwid=DEVICE_B)
        SubscriptionDevice.objects.create(subscription=self.other_vpn, hwid=DEVICE_A)

    def test_first_reset_clears_only_the_requesting_user(self):
        done, remaining = reset_devices(self.user.id)

        self.assertTrue(done)
        self.assertIsNone(remaining)
        self.assertFalse(SubscriptionDevice.objects.filter(subscription=self.user_vpn).exists())
        self.assertTrue(SubscriptionDevice.objects.filter(subscription=self.other_vpn).exists())

    def test_second_reset_within_the_cooldown_is_refused(self):
        reset_devices(self.user.id)
        SubscriptionDevice.objects.create(subscription=self.user_vpn, hwid=DEVICE_C)

        done, remaining = reset_devices(self.user.id)

        self.assertFalse(done)
        self.assertGreater(remaining, timedelta(hours=23))
        self.assertTrue(SubscriptionDevice.objects.filter(subscription=self.user_vpn).exists())

    def test_reset_is_allowed_again_after_the_cooldown(self):
        reset_devices(self.user.id)
        SubscriptionDeviceReset.objects.filter(telegram_user=self.user).update(
            last_reset_at=timezone.now() - timedelta(hours=25))
        SubscriptionDevice.objects.create(subscription=self.user_vpn, hwid=DEVICE_C)

        done, remaining = reset_devices(self.user.id)

        self.assertTrue(done)
        self.assertIsNone(remaining)
        self.assertFalse(SubscriptionDevice.objects.filter(subscription=self.user_vpn).exists())


class DeviceResetHandlerTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = SimpleNamespace(id=10, telegram_id=1001)
        self.context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        self.update = SimpleNamespace()

    @patch('apps.telegram_bot.handlers.reset_devices.reset_user_devices')
    @patch('apps.telegram_bot.handlers.reset_devices.get_user', new_callable=AsyncMock)
    async def test_successful_reset_confirms_in_russian(self, get_user, reset):
        get_user.return_value = self.user
        reset.return_value = (True, None)

        await reset_devices_handler(self.update, self.context)

        message = self.context.bot.send_message.await_args.kwargs['text']
        self.assertIn('Устройства отвязаны', message)
        reset.assert_called_once_with(10)

    @patch('apps.telegram_bot.handlers.reset_devices.reset_user_devices')
    @patch('apps.telegram_bot.handlers.reset_devices.get_user', new_callable=AsyncMock)
    async def test_refusal_states_when_the_user_may_retry(self, get_user, reset):
        get_user.return_value = self.user
        reset.return_value = (False, timedelta(hours=7, minutes=20))

        await reset_devices_handler(self.update, self.context)

        message = self.context.bot.send_message.await_args.kwargs['text']
        self.assertIn('раз в сутки', message)
        self.assertIn('7 ч. 20 мин.', message)
