import os
import subprocess
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from django.core import management
from django.test import TestCase, override_settings

from apps.monitoring.models import MonitorState, MonitorTransition
from apps.monitoring.probes import (
    LayerResult,
    build_xray_config,
    run_control_plane_probe,
    run_protocol_canary,
    run_regional_probe,
)
from apps.monitoring.tasks import _run


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


class BeatScheduleTests(TestCase):
    project_root = Path(__file__).resolve().parents[2]

    def _schedule_keys(self, *, monitor_enabled: bool, l2_enabled: bool) -> set[str]:
        environment = os.environ | {
            'SPECIAL_MONITOR_ENABLED': str(monitor_enabled).lower(),
            'SPECIAL_MONITOR_L2_ENABLED': str(l2_enabled).lower(),
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

        self.assertEqual(schedule, {'update_user_vpn_daily', 'sync_expiry_times_daily'})

    def test_l2_schedule_requires_both_monitoring_flags(self):
        without_l2 = self._schedule_keys(monitor_enabled=True, l2_enabled=False)
        with_l2 = self._schedule_keys(monitor_enabled=True, l2_enabled=True)

        self.assertEqual(without_l2, {'special_monitor_l0', 'special_monitor_l1', 'update_user_vpn_daily', 'sync_expiry_times_daily'})
        self.assertEqual(with_l2, without_l2 | {'special_monitor_l2'})


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
