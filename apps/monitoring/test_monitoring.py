import asyncio
import os
import subprocess
import sys
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from django.core import management
from django.test import TestCase, override_settings
from django.utils import timezone
from telegram.error import BadRequest, InvalidToken, NetworkError

from apps.analytics.choices import (
    CashBasisChoices,
    DateBasisChoices,
    EconomicClassChoices,
    MoneyEventKindChoices,
)
from apps.analytics.models import MoneyEvent
from apps.monitoring.models import MonitorState, MonitorTransition
from apps.monitoring.notifications import build_transition_payload, send_transition_notification
from apps.monitoring.probes import (
    LayerResult,
    build_xray_config,
    cash_gap_days,
    get_canary_subscription,
    probe_invoice_link,
    run_checkout_probe,
    run_control_plane_probe,
    run_host_capacity_probe,
    run_protocol_canary,
    run_regional_probe,
)
from apps.monitoring.tasks import _run, _notify_transition, run_checkout_monitor
from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.servers.models import TariffServer
from apps.users.models import TelegramUser


class RegionalProbeTests(TestCase):
    @override_settings(
        SPECIAL_MONITOR_PROBE_REGION='ru-bot',
        SPECIAL_MONITOR_ENDPOINTS=[
            {
                'name': 'relay',
                'host': 'relay.invalid',
                'port': 443,
                'target_region': 'ru-relay',
                'transport': 'vless-reality-tcp',
            }
        ],
    )
    @patch('apps.monitoring.probes.probe_tcp', return_value=(True, 12.5, None))
    def test_reports_only_aggregate_endpoint_metadata(self, probe_tcp):
        result = run_regional_probe()

        self.assertTrue(result.ok)
        self.assertEqual(result.details['probe_region'], 'ru-bot')
        self.assertEqual(result.details['endpoints'][0]['name'], 'relay')
        self.assertNotIn('host', result.details['endpoints'][0])
        probe_tcp.assert_called_once_with('relay.invalid', 443, 5.0)

    @override_settings(SPECIAL_MONITOR_ENDPOINTS=[])
    def test_empty_matrix_is_not_configured(self):
        result = run_regional_probe()

        self.assertFalse(result.ok)
        self.assertEqual(result.error_class, 'not_configured')


# Задачи, которые стоят в расписании всегда: их включает не флаг мониторинга, а
# наличие собственной конфигурации, и каждая сама выходит без неё.
_BASE_SCHEDULE = {
    'update_user_vpn_daily',
    'sync_expiry_times_daily',
    'poll_cryptobot_invoices',
}


class BeatScheduleTests(TestCase):
    project_root = Path(__file__).resolve().parents[2]

    def _schedule_keys(self, *, monitor_enabled: bool, l2_enabled: bool, checkout_enabled: bool = False) -> set[str]:
        environment = os.environ | {
            'SPECIAL_MONITOR_ENABLED': str(monitor_enabled).lower(),
            'SPECIAL_MONITOR_L2_ENABLED': str(l2_enabled).lower(),
            'SPECIAL_MONITOR_CHECKOUT_ENABLED': str(checkout_enabled).lower(),
        }
        result = subprocess.run(
            [
                sys.executable,
                '-c',
                'from bot.settings import CELERY_BEAT_SCHEDULE; print("\\n".join(sorted(CELERY_BEAT_SCHEDULE)))',
            ],
            cwd=self.project_root,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        return set(result.stdout.splitlines())

    def test_json_defaults_are_lists(self):
        environment = os.environ.copy()
        environment.pop('SPECIAL_MONITOR_ENDPOINTS', None)
        environment.pop('SPECIAL_MONITOR_EXPECTED_INBOUNDS', None)
        result = subprocess.run(
            [
                sys.executable,
                '-c',
                'from bot.settings import SPECIAL_MONITOR_ENDPOINTS, SPECIAL_MONITOR_EXPECTED_INBOUNDS; '
                'print(type(SPECIAL_MONITOR_ENDPOINTS).__name__); '
                'print(type(SPECIAL_MONITOR_EXPECTED_INBOUNDS).__name__)',
            ],
            cwd=self.project_root,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.stdout.splitlines(), ['list', 'list'])

    def test_monitoring_schedules_are_disabled_by_default(self):
        schedule = self._schedule_keys(monitor_enabled=False, l2_enabled=False)

        self.assertEqual(schedule, _BASE_SCHEDULE)

    def test_l2_schedule_requires_both_monitoring_flags(self):
        without_l2 = self._schedule_keys(monitor_enabled=True, l2_enabled=False)
        with_l2 = self._schedule_keys(monitor_enabled=True, l2_enabled=True)

        self.assertEqual(
            without_l2,
            _BASE_SCHEDULE | {
                'special_monitor_host',
                'special_monitor_l0',
                'special_monitor_l1',
            },
        )
        self.assertEqual(with_l2, without_l2 | {'special_monitor_l2'})

    def test_checkout_schedule_requires_both_monitoring_flags(self):
        monitoring_off = self._schedule_keys(monitor_enabled=False, l2_enabled=False, checkout_enabled=True)
        monitoring_on = self._schedule_keys(monitor_enabled=True, l2_enabled=False, checkout_enabled=True)

        self.assertNotIn('special_monitor_checkout', monitoring_off)
        self.assertEqual(
            monitoring_on,
            self._schedule_keys(monitor_enabled=True, l2_enabled=False) | {'special_monitor_checkout'},
        )


class ControlPlaneProbeTests(TestCase):
    @override_settings(
        SPECIAL_MONITOR_EXPECTED_INBOUNDS=[
            {
                'server_id': 1,
                'inbound_id': 5,
                'port': 8443,
                'protocol': 'vless',
                'network': 'tcp',
                'security': 'reality',
            }
        ]
    )
    @patch(
        'apps.monitoring.probes.fetch_control_plane_client_ids',
        new_callable=AsyncMock,
        return_value=(set(), set()),
    )
    @patch('apps.monitoring.probes.get_server_entitlement', return_value=(0, set()))
    @patch('apps.monitoring.probes.fetch_inbound_snapshots', new_callable=AsyncMock, return_value=[])
    @patch('apps.monitoring.probes.Server.objects.select_related')
    def test_missing_expected_inbound_alerts_immediately(
        self,
        select_related,
        fetch_inbounds,
        get_entitlement,
        fetch_clients,
    ):
        server = Mock(id=1)
        select_related.return_value.order_by.return_value = [server]

        result = run_control_plane_probe()

        self.assertFalse(result.ok)
        self.assertTrue(result.immediate)
        self.assertEqual(result.error_class, 'inbound_inventory_drift')

    @patch('apps.monitoring.probes.fetch_inbound_snapshots', new_callable=AsyncMock)
    @patch('apps.monitoring.probes.Server.objects.select_related')
    def test_unstable_inventory_fails_closed_as_control_plane(self, select_related, fetch_inbounds):
        server = Mock(id=1)
        select_related.return_value.order_by.return_value = [server]
        fetch_inbounds.side_effect = RuntimeError('inventory consistency')

        result = run_control_plane_probe()

        self.assertFalse(result.ok)
        self.assertFalse(result.immediate)
        self.assertEqual(result.error_class, 'control_plane')


class HostCapacityProbeTests(TestCase):
    @override_settings(
        SPECIAL_MONITOR_MIN_AVAILABLE_MB=128,
        SPECIAL_MONITOR_MIN_SWAP_MB=512,
        SPECIAL_MONITOR_MAX_LOAD_PER_CPU=4.0,
        SPECIAL_MONITOR_MAX_OOM_KILLS=0,
    )
    @patch('apps.monitoring.probes._read_oom_kill_count', return_value=0)
    @patch('apps.monitoring.probes.os.cpu_count', return_value=1)
    @patch('apps.monitoring.probes.os.getloadavg', return_value=(0.5, 0.5, 0.5))
    @patch(
        'apps.monitoring.probes._read_meminfo',
        return_value={'MemAvailable': 256 * 1024, 'SwapTotal': 1024 * 1024, 'SwapFree': 900 * 1024},
    )
    def test_healthy_host_emits_aggregate_metrics(self, *_mocks):
        result = run_host_capacity_probe()

        self.assertTrue(result.ok)
        self.assertEqual(result.details['mem_available_mb'], 256)
        self.assertEqual(result.details['swap_total_mb'], 1024)
        self.assertNotIn('processes', result.details)

    @override_settings(
        SPECIAL_MONITOR_MIN_AVAILABLE_MB=128,
        SPECIAL_MONITOR_MIN_SWAP_MB=512,
        SPECIAL_MONITOR_MAX_LOAD_PER_CPU=4.0,
        SPECIAL_MONITOR_MAX_OOM_KILLS=0,
    )
    @patch('apps.monitoring.probes._read_oom_kill_count', return_value=1)
    @patch('apps.monitoring.probes.os.cpu_count', return_value=1)
    @patch('apps.monitoring.probes.os.getloadavg', return_value=(0.5, 0.5, 0.5))
    @patch(
        'apps.monitoring.probes._read_meminfo',
        return_value={'MemAvailable': 256 * 1024, 'SwapTotal': 1024 * 1024, 'SwapFree': 900 * 1024},
    )
    def test_oom_is_immediate_failure(self, *_mocks):
        result = run_host_capacity_probe()

        self.assertFalse(result.ok)
        self.assertTrue(result.immediate)
        self.assertEqual(result.error_class, 'oom_kill')


class XrayConfigTests(TestCase):
    def test_grpc_link_preserves_transport_settings(self):
        config = build_xray_config(
            'vless://fixture@example.invalid:443?type=grpc&security=reality&serviceName=special&authority=edge.invalid&mode=multi&sni=cover.invalid&fp=chrome&pbk=fixture&sid=01',
            32001,
        )

        stream = config['outbounds'][0]['streamSettings']
        self.assertEqual(stream['network'], 'grpc')
        self.assertEqual(
            stream['grpcSettings'],
            {'serviceName': 'special', 'authority': 'edge.invalid', 'multiMode': True},
        )

    def test_websocket_tls_link_preserves_tls_path_and_host(self):
        config = build_xray_config(
            'vless://fixture@example.invalid:443?type=ws&security=tls&sni=tls.invalid&fp=chrome&path=%2Fvpn&host=edge.invalid',
            32001,
        )

        stream = config['outbounds'][0]['streamSettings']
        self.assertEqual(stream['wsSettings'], {'path': '/vpn', 'headers': {'Host': 'edge.invalid'}})
        self.assertEqual(
            stream['tlsSettings'],
            {'serverName': 'tls.invalid', 'fingerprint': 'chrome', 'allowInsecure': False},
        )


class ProtocolCanaryConfigurationTests(TestCase):
    @override_settings(SPECIAL_MONITOR_L2_ENABLED=True, SPECIAL_MONITOR_EXPECTED_EGRESS='')
    @patch('apps.monitoring.probes.UserVPN.objects.select_related')
    def test_unset_expected_egress_fails_closed_before_reading_canary(self, select_related):
        result = run_protocol_canary()

        self.assertFalse(result.ok)
        self.assertEqual(result.error_class, 'not_configured')
        self.assertEqual(result.details, {'status': 'expected_egress_unset'})
        select_related.assert_not_called()

    @override_settings(
        SPECIAL_MONITOR_L2_ENABLED=True,
        SPECIAL_MONITOR_EXPECTED_EGRESS='not-an-ip',
        SPECIAL_MONITOR_HEALTH_URL='https://api.ipify.org',
    )
    def test_invalid_expected_egress_is_not_configured(self):
        result = run_protocol_canary()

        self.assertEqual(result.error_class, 'not_configured')
        self.assertEqual(result.details, {'status': 'invalid_egress'})

    @override_settings(
        SPECIAL_MONITOR_L2_ENABLED=True,
        SPECIAL_MONITOR_EXPECTED_EGRESS='192.0.2.1',
        SPECIAL_MONITOR_HEALTH_URL='http://api.ipify.org',
    )
    def test_non_https_health_url_is_not_configured(self):
        result = run_protocol_canary()

        self.assertEqual(result.error_class, 'not_configured')
        self.assertEqual(result.details, {'status': 'invalid_health_url'})


class CanarySubscriptionSourceTests(TestCase):
    """Канарейка читает ту же панель, что и выдача, и делает это без обмана."""

    def _user_vpn(self):
        return SimpleNamespace(
            vpn_uuid='11111111-2222-3333-4444-555555555555',
            user=SimpleNamespace(telegram_id=1),
            id=801,
        )

    @override_settings(REMNAWAVE_ENABLED=True, SUBSCRIPTION_BASE_URL='https://sub.example/sub')
    def test_active_panel_user_yields_a_link_built_from_its_own_short_uuid(self):
        panel_user = {'status': 'ACTIVE', 'vlessUuid': '11111111-2222-3333-4444-555555555555',
                      'shortUuid': 'abcdef'}
        with patch('apps.monitoring.probes.RemnawaveAPI') as api_class:
            api_class.return_value.get_user_by_username = AsyncMock(return_value=panel_user)
            url = asyncio.run(get_canary_subscription(self._user_vpn()))

        self.assertTrue(url.endswith('abcdef'))

    @override_settings(REMNAWAVE_ENABLED=True, SUBSCRIPTION_BASE_URL='https://sub.example/sub')
    def test_disabled_panel_user_is_reported_missing_not_returned(self):
        """«В базе доступ есть, в панели выключен» — ровно та авария, что ловится."""
        panel_user = {'status': 'DISABLED', 'vlessUuid': '11111111-2222-3333-4444-555555555555',
                      'shortUuid': 'abcdef'}
        with patch('apps.monitoring.probes.RemnawaveAPI') as api_class:
            api_class.return_value.get_user_by_username = AsyncMock(return_value=panel_user)
            with self.assertRaisesRegex(RuntimeError, 'canary_client_missing'):
                asyncio.run(get_canary_subscription(self._user_vpn()))

    @override_settings(REMNAWAVE_ENABLED=True, SUBSCRIPTION_BASE_URL='https://sub.example/sub')
    def test_uuid_mismatch_is_reported_missing(self):
        panel_user = {'status': 'ACTIVE', 'vlessUuid': 'other', 'shortUuid': 'abcdef'}
        with patch('apps.monitoring.probes.RemnawaveAPI') as api_class:
            api_class.return_value.get_user_by_username = AsyncMock(return_value=panel_user)
            with self.assertRaisesRegex(RuntimeError, 'canary_client_missing'):
                asyncio.run(get_canary_subscription(self._user_vpn()))


@override_settings(SPECIAL_MONITOR_FAILURE_THRESHOLD=2)
class MonitoringStateTests(TestCase):
    def test_second_failure_opens_alert_and_success_recovers(self):
        failure = LayerResult(layer='l1', ok=False, error_class='regional_reachability')
        success = LayerResult(layer='l1', ok=True, error_class=None)

        first = _run('l1', Mock(return_value=failure))
        self.assertFalse(first['ok'])
        self.assertFalse(MonitorState.objects.get(layer='l1').alert)
        self.assertEqual(MonitorTransition.objects.count(), 0)

        _run('l1', Mock(return_value=failure))
        state = MonitorState.objects.get(layer='l1')
        self.assertTrue(state.alert)
        self.assertEqual(state.consecutive_failures, 2)
        self.assertEqual(list(MonitorTransition.objects.values_list('event', flat=True)), ['opened'])

        _run('l1', Mock(return_value=success))
        state.refresh_from_db()
        self.assertFalse(state.alert)
        self.assertEqual(state.consecutive_failures, 0)
        self.assertEqual(
            list(MonitorTransition.objects.order_by('created_at').values_list('event', flat=True)),
            ['opened', 'recovered'],
        )

    def test_entitlement_failure_alerts_immediately(self):
        result = LayerResult(layer='l0', ok=False, error_class='entitled_missing', immediate=True)

        _run('l0', Mock(return_value=result))

        state = MonitorState.objects.get(layer='l0')
        self.assertTrue(state.alert)
        self.assertEqual(state.consecutive_failures, 1)

    def test_task_result_and_state_do_not_store_secret_probe_input(self):
        result = LayerResult(
            layer='l2',
            ok=False,
            error_class='canary_protocol',
            details={'subscription_e2e': False, 'direct_legacy_e2e': False},
        )

        task_result = _run('l2', Mock(return_value=result))
        state = MonitorState.objects.get(layer='l2')
        rendered = repr((task_result, state.details))

        self.assertNotIn('vless://', rendered)
        self.assertNotIn('/sub/', rendered)
        self.assertNotIn('uuid', rendered.lower())

    def test_runner_exception_is_sanitized(self):
        task_result = _run('l2', Mock(side_effect=RuntimeError('vless://secret@host/sub/bearer')))

        self.assertEqual(task_result, {'layer': 'l2', 'ok': False, 'error_class': 'runner_failure'})
        state = MonitorState.objects.get(layer='l2')
        self.assertEqual(state.error_class, 'runner_failure')
        self.assertEqual(state.details, {})

    @override_settings(SPECIAL_MONITOR_PAGING_ENABLED=False, SPECIAL_MONITOR_PAGING_OWNER='')
    def test_disabled_notification_records_only_error_class(self):
        transition = MonitorTransition.objects.create(
            layer='l1',
            event='opened',
            error_class='regional_reachability',
            consecutive_failures=2,
        )

        _notify_transition(transition.pk)

        transition.refresh_from_db()
        self.assertIsNotNone(transition.notification_attempted_at)
        self.assertFalse(transition.notification_delivered)
        self.assertEqual(transition.notification_error_class, 'disabled')

    def test_notification_payload_is_aggregate_only(self):
        transition = MonitorTransition.objects.create(
            layer='l0',
            event='opened',
            error_class='entitled_missing',
            consecutive_failures=1,
        )
        payload = build_transition_payload(
            layer=transition.layer,
            event=transition.event,
            error_class=transition.error_class,
            failures=transition.consecutive_failures,
            created_at=transition.created_at,
        )
        rendered = repr(payload)

        self.assertNotIn('uuid', rendered.lower())
        self.assertNotIn('/sub/', rendered)
        self.assertEqual(payload['service'], 'special-bot')

    @override_settings(
        SPECIAL_MONITOR_PAGING_ENABLED=True,
        SPECIAL_MONITOR_PAGING_WEBHOOK_URL='http://paging.invalid/hook',
        SPECIAL_MONITOR_PAGING_OWNER='primary-on-call',
    )
    def test_paging_rejects_non_https_webhook(self):
        result = send_transition_notification({'service': 'special-bot'})

        self.assertFalse(result.delivered)
        self.assertEqual(result.error_class, 'not_configured')

    def test_checkout_transition_is_a_payable_notification_layer(self):
        payload = build_transition_payload(
            layer='checkout',
            event='opened',
            error_class='cash_gap',
            failures=2,
            created_at=timezone.now(),
        )

        self.assertEqual(payload['layer'], 'checkout')
        self.assertEqual(payload['error_class'], 'cash_gap')

    def test_status_command_is_read_only_and_excludes_details(self):
        MonitorState.objects.create(
            layer='l2',
            last_ok=False,
            alert=True,
            consecutive_failures=2,
            error_class='canary_protocol',
            details={'private': 'not-rendered'},
        )
        output = StringIO()

        management.call_command('audit_special_monitoring', '--json', stdout=output)

        rendered = output.getvalue()
        self.assertIn('"layer": "l2"', rendered)
        self.assertNotIn('private', rendered)
        self.assertNotIn('not-rendered', rendered)


PROBE_PROVIDER_TOKEN = 'fixture-provider-token'
# Bot API wording, with the token spliced into the message the way a future
# Telegram release could splice anything else in. The probe must key on the
# identifier and carry neither the wording nor the token out.
PROVIDER_REJECTION = BadRequest(f'Bad Request: PAYMENT_PROVIDER_INVALID {PROBE_PROVIDER_TOKEN}')
AMOUNT_REJECTION = BadRequest('Bad Request: CURRENCY_TOTAL_AMOUNT_INVALID')
CURRENCY_REJECTION = BadRequest('Bad Request: CURRENCY_INVALID')
UNMAPPED_REJECTION = BadRequest(f'Bad Request: NOT_A_KNOWN_IDENTIFIER {PROBE_PROVIDER_TOKEN}')


class CashGapTests(TestCase):
    def setUp(self):
        self.user = TelegramUser.objects.create(telegram_id=9001, username='cash-gap')

    def money_event(self, days_ago: int, *, cash: str = '0', credit: str = '0') -> None:
        occurred_at = timezone.now() - timedelta(days=days_ago)
        MoneyEvent.objects.create(
            event_key=f'fixture:{days_ago}:{cash}:{credit}',
            occurred_at=occurred_at,
            effective_date=occurred_at.date(),
            user=self.user,
            source=TransactionSourceChoices.YOUMONEY,
            status=TransactionStatusChoices.SUCCESS,
            kind=MoneyEventKindChoices.TOPUP,
            economic_class=EconomicClassChoices.CASH_IN,
            cash_basis=CashBasisChoices.MEASURED,
            date_basis=DateBasisChoices.CREATED_AT,
            balance_delta=Decimal(cash) + Decimal(credit),
            cash_amount=Decimal(cash),
            credit_amount=Decimal(credit),
        )

    def test_no_cash_in_at_all_is_unknown_rather_than_zero(self):
        self.assertIsNone(cash_gap_days())

    def test_cash_in_today_closes_the_gap(self):
        self.money_event(0, cash='420')

        self.assertEqual(cash_gap_days(), 0)

    def test_gap_counts_from_the_most_recent_cash_in(self):
        self.money_event(9, cash='420')
        self.money_event(30, cash='210')

        self.assertEqual(cash_gap_days(), 9)

    def test_issued_credit_does_not_look_like_cash(self):
        # The nine-day silence came with 3 150 ₽ of credits issued. Counting
        # those would have hidden exactly the outage this layer exists for.
        self.money_event(0, credit='3150')
        self.money_event(9, cash='420')

        self.assertEqual(cash_gap_days(), 9)


@override_settings(
    TELEGRAM_BOT_TOKEN='fixture-bot-token',
    YOUMONEY_TOKEN=PROBE_PROVIDER_TOKEN,
    SPECIAL_MONITOR_CHECKOUT_TIMEOUT=1,
    SPECIAL_MONITOR_CHECKOUT_AMOUNT=10000,
    SPECIAL_MONITOR_CASH_GAP_DAYS=3,
)
class CheckoutProbeTests(TestCase):
    def setUp(self):
        # The one row the top-up handler's bare `aget()` needs to survive.
        TariffServer.objects.create(name='base', price=Decimal('7.00'))

    @override_settings(YOUMONEY_TOKEN='')
    @patch('apps.monitoring.probes.Bot')
    def test_missing_provider_token_never_reaches_bot_api(self, bot):
        self.assertEqual(probe_invoice_link(1.0), 'not_configured')
        bot.assert_not_called()

    def test_a_revoked_bot_token_still_shuts_the_client_down(self):
        # `initialize` fails on `get_me` before the invoice call is reached.
        # Without shutdown, every run leaks a live client onto a closed loop.
        bot = Mock()
        bot.initialize = AsyncMock(side_effect=InvalidToken('The token `secret` was rejected by the server.'))
        bot.create_invoice_link = AsyncMock()
        bot.shutdown = AsyncMock()

        with patch('apps.monitoring.probes.Bot', return_value=bot):
            error_class = probe_invoice_link(1.0)

        self.assertEqual(error_class, 'InvalidToken')
        self.assertNotIn('secret', error_class)
        bot.shutdown.assert_awaited_once()
        bot.create_invoice_link.assert_not_awaited()

    @patch('apps.monitoring.probes.create_probe_invoice_link', new_callable=AsyncMock)
    def test_a_revoked_provider_token_is_named_as_such(self, invoice):
        invoice.side_effect = PROVIDER_REJECTION

        self.assertEqual(probe_invoice_link(1.0), 'provider_token_rejected')

    @patch('apps.monitoring.probes.create_probe_invoice_link', new_callable=AsyncMock)
    def test_a_rejected_amount_is_the_probe_not_the_checkout(self, invoice):
        invoice.side_effect = AMOUNT_REJECTION

        self.assertEqual(probe_invoice_link(1.0), 'invoice_amount_rejected')

    @patch('apps.monitoring.probes.create_probe_invoice_link', new_callable=AsyncMock)
    def test_a_rejected_currency_is_named_separately(self, invoice):
        invoice.side_effect = CURRENCY_REJECTION

        self.assertEqual(probe_invoice_link(1.0), 'invoice_currency_rejected')

    @patch('apps.monitoring.probes.create_probe_invoice_link', new_callable=AsyncMock)
    def test_an_unmapped_rejection_keeps_the_class_and_drops_the_text(self, invoice):
        invoice.side_effect = UNMAPPED_REJECTION

        error_class = probe_invoice_link(1.0)

        self.assertEqual(error_class, 'BadRequest')
        self.assertNotIn(PROBE_PROVIDER_TOKEN, error_class)
        self.assertNotIn('NOT_A_KNOWN_IDENTIFIER', error_class)

    @patch('apps.monitoring.probes.create_probe_invoice_link', new_callable=AsyncMock)
    def test_a_bad_bot_token_stays_distinct_from_a_bad_provider_token(self, invoice):
        # The two are the same alert but not the same repair, and the operator
        # gets only this string.
        invoice.side_effect = InvalidToken('Unauthorized')

        self.assertEqual(probe_invoice_link(1.0), 'InvalidToken')

    @patch('apps.monitoring.probes.create_probe_invoice_link', new_callable=AsyncMock)
    def test_telegram_being_unreachable_is_not_a_provider_verdict(self, invoice):
        invoice.side_effect = NetworkError('httpx.ConnectError')

        self.assertEqual(probe_invoice_link(1.0), 'NetworkError')

    def test_a_stalled_invoice_call_hands_the_worker_back(self):
        async def never_returns(_timeout):
            await asyncio.sleep(5)

        with patch('apps.monitoring.probes.create_probe_invoice_link', never_returns):
            self.assertEqual(probe_invoice_link(0.05), 'invoice_timeout')

    @patch('apps.monitoring.probes.cash_gap_days', return_value=0)
    @patch('apps.monitoring.probes.create_probe_invoice_link', new_callable=AsyncMock)
    def test_working_checkout_and_recent_cash_is_healthy(self, invoice, gap):
        result = run_checkout_probe()

        self.assertTrue(result.ok)
        self.assertIsNone(result.error_class)
        self.assertEqual(result.details['cash_gap_days'], 0)

    @patch('apps.monitoring.probes.cash_gap_days', return_value=0)
    @patch('apps.monitoring.probes.create_probe_invoice_link', new_callable=AsyncMock)
    def test_failing_probe_with_zero_gap_is_a_checkout_failure(self, invoice, gap):
        invoice.side_effect = PROVIDER_REJECTION

        result = run_checkout_probe()

        self.assertFalse(result.ok)
        self.assertEqual(result.error_class, 'provider_token_rejected')
        self.assertFalse(result.details['invoice_ok'])
        self.assertEqual(result.details['cash_gap_days'], 0)

    @patch('apps.monitoring.probes.cash_gap_days', return_value=9)
    @patch('apps.monitoring.probes.create_probe_invoice_link', new_callable=AsyncMock)
    def test_healthy_probe_with_a_grown_gap_is_reported_as_a_gap(self, invoice, gap):
        result = run_checkout_probe()

        self.assertFalse(result.ok)
        self.assertEqual(result.error_class, 'cash_gap')
        self.assertTrue(result.details['invoice_ok'])
        self.assertEqual(result.details['cash_gap_days'], 9)

    @patch('apps.monitoring.probes.cash_gap_days', return_value=None)
    @patch('apps.monitoring.probes.create_probe_invoice_link', new_callable=AsyncMock)
    def test_never_having_taken_cash_does_not_page(self, invoice, gap):
        result = run_checkout_probe()

        self.assertTrue(result.ok)
        self.assertIsNone(result.details['cash_gap_days'])

    @patch('apps.monitoring.probes.cash_gap_days', return_value=0)
    @patch('apps.monitoring.probes.create_probe_invoice_link', new_callable=AsyncMock)
    def test_an_empty_tariff_table_is_a_checkout_failure_not_a_green_probe(self, invoice, gap):
        # The customer taps an amount and the handler's bare `aget()` raises
        # before `send_invoice`. The provider is fine; checkout is not.
        TariffServer.objects.all().delete()

        result = run_checkout_probe()

        self.assertFalse(result.ok)
        self.assertEqual(result.error_class, 'tariff_missing')
        self.assertFalse(result.details['tariff_ok'])
        self.assertTrue(result.details['invoice_ok'])

    @patch('apps.monitoring.probes.cash_gap_days', return_value=0)
    @patch('apps.monitoring.probes.create_probe_invoice_link', new_callable=AsyncMock)
    def test_a_second_tariff_row_breaks_checkout_just_as_loudly(self, invoice, gap):
        TariffServer.objects.create(name='second', price=Decimal('9.00'))

        result = run_checkout_probe()

        self.assertFalse(result.ok)
        self.assertEqual(result.error_class, 'tariff_ambiguous')

    @patch('apps.monitoring.probes.cash_gap_days', return_value=0)
    @patch('apps.monitoring.probes.create_probe_invoice_link', new_callable=AsyncMock)
    def test_our_own_lookup_is_blamed_before_the_provider(self, invoice, gap):
        # Both broken at once: the operator must not be sent to the provider
        # for a failure that is ours, and must still see the provider verdict.
        TariffServer.objects.all().delete()
        invoice.side_effect = PROVIDER_REJECTION

        result = run_checkout_probe()

        self.assertEqual(result.error_class, 'tariff_missing')
        self.assertEqual(result.details['invoice_error_class'], 'provider_token_rejected')

    @patch('apps.monitoring.probes.cash_gap_days', return_value=0)
    @patch('apps.monitoring.probes.create_probe_invoice_link', new_callable=AsyncMock)
    def test_state_keeps_neither_the_token_nor_the_rejection_text(self, invoice, gap):
        invoice.side_effect = PROVIDER_REJECTION

        task_result = _run('checkout', run_checkout_probe)
        state = MonitorState.objects.get(layer='checkout')
        rendered = repr((task_result, state.details, state.error_class))

        self.assertIn('provider_token_rejected', rendered)
        self.assertNotIn(PROBE_PROVIDER_TOKEN, rendered)
        self.assertNotIn('Bad Request', rendered)
        self.assertNotIn('t.me', rendered)


class CheckoutMonitorTaskTests(TestCase):
    @override_settings(SPECIAL_MONITOR_CHECKOUT_ENABLED=False)
    @patch('apps.monitoring.tasks.run_checkout_probe')
    def test_disabled_flag_probes_nothing_and_records_nothing(self, probe):
        result = run_checkout_monitor()

        probe.assert_not_called()
        self.assertTrue(result['skipped'])
        self.assertFalse(MonitorState.objects.filter(layer='checkout').exists())
        self.assertEqual(MonitorTransition.objects.count(), 0)

    @override_settings(SPECIAL_MONITOR_CHECKOUT_ENABLED=True, SPECIAL_MONITOR_FAILURE_THRESHOLD=2)
    @patch(
        'apps.monitoring.tasks.run_checkout_probe',
        return_value=LayerResult(layer='checkout', ok=False, error_class='cash_gap'),
    )
    def test_enabled_flag_records_state_and_opens_on_threshold(self, probe):
        run_checkout_monitor()
        self.assertFalse(MonitorState.objects.get(layer='checkout').alert)

        self.assertEqual(run_checkout_monitor()['error_class'], 'cash_gap')
        self.assertTrue(MonitorState.objects.get(layer='checkout').alert)
        self.assertEqual(
            list(MonitorTransition.objects.values_list('layer', 'event')),
            [('checkout', 'opened')],
        )
