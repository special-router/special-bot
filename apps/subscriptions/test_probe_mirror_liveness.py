import io
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings, TestCase

from apps.subscriptions.management.commands.probe_mirror_liveness import Command
from apps.subscriptions.models import MirrorEndpointLiveness


ENDPOINT = {
    'host': 'ru-1.example', 'port': 443, 'uuid': 'synthetic-id', 'security': 'reality',
    'public_key': 'synthetic-pbk', 'short_id': 'ab01', 'server_name': 'sni.example',
    'fingerprint': 'firefox', 'network': 'tcp', 'service_name': '', 'path': '', 'flow': '',
}


@override_settings(SPECIAL_MONITOR_XRAY_PATH='/bin/true')
class ProbeMirrorLivenessTests(TestCase):
    """The prober is an instrument, so what it refuses to conclude matters most.

    Every verdict it writes removes a candidate from a customer's list, and the
    failure it is most likely to hit is its own: no xray, no egress, no source.
    """

    def run_command(self, results, **options):
        with patch.object(Command, '_targets', return_value=[ENDPOINT] * len(results)), \
                patch.object(Command, '_probe_one', side_effect=results):
            output = io.StringIO()
            call_command('probe_mirror_liveness', stdout=output, **options)
        return output.getvalue()

    def test_an_xray_that_cannot_run_refuses_the_whole_run(self):
        """Otherwise the first scheduled run marks a working fleet dead."""
        with override_settings(SPECIAL_MONITOR_XRAY_PATH='/nonexistent/xray'):
            with self.assertRaises(CommandError):
                call_command('probe_mirror_liveness', stdout=io.StringIO())

        self.assertEqual(MirrorEndpointLiveness.objects.count(), 0)

    def test_a_run_where_nothing_answered_writes_no_verdict(self):
        output = self.run_command([('ru-1.example', 443, False, 'timeout'),
                                   ('ru-2.example', 443, False, 'ConnectionResetError')])

        self.assertIn('writing nothing', output)
        self.assertEqual(MirrorEndpointLiveness.objects.count(), 0)

    def test_a_verdict_is_written_per_address_and_refreshed_in_place(self):
        self.run_command([('ru-1.example', 443, True, ''),
                          ('ru-2.example', 443, False, 'ConnectionResetError')])

        self.assertEqual(MirrorEndpointLiveness.objects.count(), 2)
        self.assertTrue(MirrorEndpointLiveness.objects.get(host='ru-1.example', port=443).alive)

        self.run_command([('ru-1.example', 443, True, ''),
                          ('ru-2.example', 443, True, '')])

        self.assertEqual(MirrorEndpointLiveness.objects.count(), 2)
        self.assertTrue(MirrorEndpointLiveness.objects.get(host='ru-2.example', port=443).alive)

    def test_a_dry_run_reports_without_writing(self):
        output = self.run_command([('ru-1.example', 443, True, '')], dry_run=True)

        self.assertIn('dry run', output)
        self.assertEqual(MirrorEndpointLiveness.objects.count(), 0)

    def test_no_configured_source_probes_nothing(self):
        with override_settings(SUBSCRIPTION_BACKUP_UPSTREAM_URLS=[]):
            output = io.StringIO()
            call_command('probe_mirror_liveness', stdout=output)

        self.assertIn('no configured source', output.getvalue())
        self.assertEqual(MirrorEndpointLiveness.objects.count(), 0)

    def test_an_endpoint_reached_after_the_deadline_is_skipped_not_failed(self):
        """Out of time is not evidence of a dead server, so it produces no verdict."""
        self.assertIsNone(Command()._probe_one(ENDPOINT, '/bin/true', deadline=0.0, timeout=1.0))

    def test_the_probe_client_carries_the_endpoints_own_reality_parameters(self):
        from apps.subscriptions.management.commands.probe_mirror_liveness import _xray_config

        config = _xray_config(ENDPOINT, 10800)
        outbound = config['outbounds'][0]

        self.assertEqual(outbound['settings']['vnext'][0]['address'], 'ru-1.example')
        self.assertEqual(outbound['streamSettings']['realitySettings'],
                         {'serverName': 'sni.example', 'publicKey': 'synthetic-pbk',
                          'fingerprint': 'firefox', 'spiderX': '/', 'shortId': 'ab01'})
        self.assertEqual(config['inbounds'][0]['listen'], '127.0.0.1')
