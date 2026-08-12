import base64
from datetime import timedelta
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from apps.servers.models import Server, TariffServer
from apps.subscriptions import views
from apps.subscriptions.devices import client_hwid, open_binding_window, reset_devices
from apps.subscriptions.models import (
    SubscriptionDevice,
    SubscriptionDeviceBindingWindow,
    SubscriptionDeviceRegistrationRate,
    SubscriptionDeviceReset,
)
from apps.telegram_bot.handlers.bind_device import bind_device as bind_device_handler
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
    SUBSCRIPTION_DEVICE_BINDING_WINDOW_MINUTES=15,
    SUBSCRIPTION_DEVICE_BINDING_WINDOW_REQUIRED=True,
    SUBSCRIPTION_DEVICE_REGISTRATIONS_PER_HOUR=5,
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
        cls.user = TelegramUser.objects.create(telegram_id=1001, username='holder')
        cls.user_vpn = UserVPN.objects.create(user=cls.user, server=server, sub_id='synthetic')

    def _request(self, headers=None, sub_id='synthetic'):
        return views.subscription_proxy(
            RequestFactory().get(f'/sub/{sub_id}', headers=headers or {}), sub_id)

    def _open_window(self):
        open_binding_window(self.user.id)

    def test_first_device_binds_without_a_window(self, _params):
        response = self._request({'x-hwid': DEVICE_A})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['x-hwid-active'], 'true')
        self.assertNotIn('x-hwid-max-devices-reached', response)
        self.assertFalse(SubscriptionDeviceBindingWindow.objects.exists())
        self.assertEqual(SubscriptionDevice.objects.filter(subscription=self.user_vpn).count(), 1)

    def test_second_device_binds_inside_an_open_window(self, _params):
        self._request({'x-hwid': DEVICE_A})
        self._open_window()

        response = self._request({'x-hwid': DEVICE_B})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(SubscriptionDevice.objects.filter(subscription=self.user_vpn).count(), 2)

    def test_third_device_is_refused_with_limit_headers(self, _params):
        self._open_window()
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
        self._open_window()
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
        self._open_window()
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

        # Only the unattended first binding is spent; the rest need a window.
        self.assertEqual(SubscriptionDevice.objects.filter(subscription=self.user_vpn).count(), 1)

    def test_attacker_without_a_window_cannot_register_any_device(self, _params):
        self._request({'x-hwid': DEVICE_A})

        for index in range(10):
            response = self._request({'x-hwid': f'forged{index:06d}'})
            self.assertEqual(response.status_code, 404)

        self.assertEqual(
            list(SubscriptionDevice.objects.filter(subscription=self.user_vpn)
                 .values_list('hwid', flat=True)),
            [DEVICE_A],
        )

    def test_bound_device_keeps_working_while_an_attacker_hammers(self, _params):
        self._request({'x-hwid': DEVICE_A})

        for index in range(25):
            self._request({'x-hwid': f'forged{index:06d}'})
        response = self._request({'x-hwid': DEVICE_A})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(base64.b64decode(response.content).decode().splitlines()), 3)

    def test_attacker_cannot_drain_the_registration_budget(self, _params):
        self._request({'x-hwid': DEVICE_A})
        for index in range(30):
            self._request({'x-hwid': f'forged{index:06d}'})
        self._open_window()

        response = self._request({'x-hwid': DEVICE_B})

        # Refused registrations must never consume the owner's allowance.
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            SubscriptionDeviceRegistrationRate.objects.get(subscription=self.user_vpn).registrations, 2)

    def test_window_expiry_refuses_new_devices_again(self, _params):
        self._request({'x-hwid': DEVICE_A})
        self._open_window()
        SubscriptionDeviceBindingWindow.objects.filter(telegram_user=self.user).update(
            opened_at=timezone.now() - timedelta(minutes=16))

        response = self._request({'x-hwid': DEVICE_B})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(SubscriptionDevice.objects.filter(subscription=self.user_vpn).count(), 1)

    def test_window_of_another_user_does_not_open_this_subscription(self, _params):
        stranger = TelegramUser.objects.create(telegram_id=1002, username='stranger')
        self._request({'x-hwid': DEVICE_A})
        open_binding_window(stranger.id)

        response = self._request({'x-hwid': DEVICE_B})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(SubscriptionDevice.objects.filter(subscription=self.user_vpn).count(), 1)

    @override_settings(SUBSCRIPTION_DEVICE_BINDING_WINDOW_REQUIRED=False)
    def test_rollout_switch_binds_a_second_device_without_a_window(self, _params):
        self._request({'x-hwid': DEVICE_A})

        response = self._request({'x-hwid': DEVICE_B})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(SubscriptionDeviceBindingWindow.objects.exists())
        self.assertEqual(SubscriptionDevice.objects.filter(subscription=self.user_vpn).count(), 2)

    def test_the_same_request_is_refused_once_the_window_is_required(self, _params):
        self._request({'x-hwid': DEVICE_A})

        response = self._request({'x-hwid': DEVICE_B})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(SubscriptionDevice.objects.filter(subscription=self.user_vpn).count(), 1)

    @override_settings(SUBSCRIPTION_DEVICE_BINDING_WINDOW_REQUIRED=False)
    def test_rollout_switch_does_not_lift_the_device_limit(self, _params):
        self._request({'x-hwid': DEVICE_A})
        self._request({'x-hwid': DEVICE_B})

        response = self._request({'x-hwid': DEVICE_C})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(SubscriptionDevice.objects.filter(subscription=self.user_vpn).count(), 2)

    @override_settings(
        SUBSCRIPTION_DEVICE_BINDING_WINDOW_REQUIRED=False,
        SUBSCRIPTION_DEVICE_REGISTRATIONS_PER_HOUR=2,
    )
    def test_rollout_switch_still_obeys_the_registration_budget(self, _params):
        UserVPN.objects.filter(pk=self.user_vpn.pk).update(device_limit=5)

        self._request({'x-hwid': DEVICE_A})
        self._request({'x-hwid': DEVICE_B})
        response = self._request({'x-hwid': DEVICE_C})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(SubscriptionDevice.objects.filter(subscription=self.user_vpn).count(), 2)

    @override_settings(SUBSCRIPTION_DEVICE_REGISTRATIONS_PER_HOUR=2)
    def test_registration_rate_limit_bounds_an_open_window(self, _params):
        UserVPN.objects.filter(pk=self.user_vpn.pk).update(device_limit=5)
        self._open_window()

        self._request({'x-hwid': DEVICE_A})
        self._request({'x-hwid': DEVICE_B})
        response = self._request({'x-hwid': DEVICE_C})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(SubscriptionDevice.objects.filter(subscription=self.user_vpn).count(), 2)

    @override_settings(SUBSCRIPTION_DEVICE_REGISTRATIONS_PER_HOUR=2)
    def test_registration_budget_recovers_after_the_period(self, _params):
        UserVPN.objects.filter(pk=self.user_vpn.pk).update(device_limit=5)
        self._open_window()
        self._request({'x-hwid': DEVICE_A})
        self._request({'x-hwid': DEVICE_B})
        SubscriptionDeviceRegistrationRate.objects.filter(subscription=self.user_vpn).update(
            period_started_at=timezone.now() - timedelta(hours=2))

        response = self._request({'x-hwid': DEVICE_C})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(SubscriptionDevice.objects.filter(subscription=self.user_vpn).count(), 3)

    def test_refusals_are_indistinguishable_from_an_unknown_subscription(self, _params):
        self._request({'x-hwid': DEVICE_A})
        UserVPN.objects.create(user=self.user, server=self.user_vpn.server, sub_id='disabled',
                               enabled=False)

        refused = self._request({'x-hwid': DEVICE_B})
        unknown = self._request({'x-hwid': DEVICE_B}, sub_id='no-such-subscription')
        disabled = self._request({'x-hwid': DEVICE_B}, sub_id='disabled')

        # A guessed sub_id must not be confirmable by the shape of its refusal.
        for other in (unknown, disabled):
            self.assertEqual(other.status_code, refused.status_code)
            self.assertEqual(other.content, refused.content)
            self.assertEqual(sorted(other.items()), sorted(refused.items()))

    @override_settings(SUBSCRIPTION_HWID_STRICT=True)
    def test_strict_refusals_are_indistinguishable_without_an_identifier(self, _params):
        refused = self._request()
        unknown = self._request(sub_id='no-such-subscription')

        self.assertEqual(unknown.status_code, refused.status_code)
        self.assertEqual(sorted(unknown.items()), sorted(refused.items()))


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


@override_settings(
    SUBSCRIPTION_DEVICE_RESET_COOLDOWN_HOURS=1,
    SUBSCRIPTION_DEVICE_BINDING_WINDOW_MINUTES=15,
)
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
        self.assertGreater(remaining, timedelta(minutes=50))
        self.assertTrue(SubscriptionDevice.objects.filter(subscription=self.user_vpn).exists())

    def test_reset_is_allowed_again_after_the_cooldown(self):
        reset_devices(self.user.id)
        SubscriptionDeviceReset.objects.filter(telegram_user=self.user).update(
            last_reset_at=timezone.now() - timedelta(hours=2))
        SubscriptionDevice.objects.create(subscription=self.user_vpn, hwid=DEVICE_C)

        done, remaining = reset_devices(self.user.id)

        self.assertTrue(done)
        self.assertIsNone(remaining)
        self.assertFalse(SubscriptionDevice.objects.filter(subscription=self.user_vpn).exists())

    def test_reset_opens_the_binding_window_for_the_requesting_user(self):
        reset_devices(self.user.id)

        # Clearing without re-opening would leave the user unable to re-bind.
        self.assertTrue(SubscriptionDeviceBindingWindow.objects.filter(
            telegram_user=self.user).exists())
        self.assertFalse(SubscriptionDeviceBindingWindow.objects.filter(
            telegram_user=self.other_user).exists())


class DeviceResetHandlerTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = SimpleNamespace(id=10, telegram_id=1001)
        self.context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        self.update = SimpleNamespace()

    @patch('apps.telegram_bot.handlers.reset_devices.render_screen', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.reset_devices.build_keys_screen', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.reset_devices.reset_user_devices')
    @patch('apps.telegram_bot.handlers.reset_devices.get_user', new_callable=AsyncMock)
    async def test_successful_reset_confirms_in_russian(self, get_user, reset, build_screen, _render):
        get_user.return_value = self.user
        reset.return_value = (True, None)
        build_screen.return_value = ('экран', None)

        await reset_devices_handler(self.update, self.context)

        message = build_screen.await_args.kwargs['notice']
        self.assertIn('Устройства отвязаны', message)
        reset.assert_called_once_with(10)

    @patch('apps.telegram_bot.handlers.reset_devices.render_screen', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.reset_devices.build_keys_screen', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.reset_devices.reset_user_devices')
    @patch('apps.telegram_bot.handlers.reset_devices.get_user', new_callable=AsyncMock)
    async def test_refusal_states_when_the_user_may_retry(self, get_user, reset, build_screen, _render):
        get_user.return_value = self.user
        reset.return_value = (False, timedelta(hours=7, minutes=20))
        build_screen.return_value = ('экран', None)

        await reset_devices_handler(self.update, self.context)

        message = build_screen.await_args.kwargs['notice']
        self.assertIn('не так часто', message)
        self.assertIn('7 ч. 20 мин.', message)
        # Раньше отказ отправлял к кнопке «Привязать устройство»; её больше нет,
        # и совет обязан говорить о том, что происходит без неё.
        self.assertNotIn('Привязать устройство', message)
        self.assertIn('привязанные устройства', message)


class BindDeviceHandlerTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = SimpleNamespace(id=10, telegram_id=1001)
        self.context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        self.update = SimpleNamespace()

    @patch('apps.telegram_bot.handlers.bind_device.render_screen', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.bind_device.build_keys_screen', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.bind_device.open_binding_window')
    @patch('apps.telegram_bot.handlers.bind_device.get_user', new_callable=AsyncMock)
    async def test_binding_window_is_opened_for_the_authenticated_user(
        self, get_user, opener, build_screen, _render
    ):
        get_user.return_value = self.user
        opener.return_value = timedelta(minutes=15)
        build_screen.return_value = ('экран', None)

        await bind_device_handler(self.update, self.context)

        opener.assert_called_once_with(10)
        message = build_screen.await_args.kwargs['notice']
        self.assertIn('15 мин.', message)
        self.assertIn('подписк', message)
        self.assertNotIn('ключ', message)
