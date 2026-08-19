import datetime
import io
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings, TestCase
from django.utils import timezone

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

    def test_a_verdict_is_stamped_with_the_configured_probe_origin(self):
        with override_settings(SUBSCRIPTION_BACKUP_LIVENESS_PROBE_ORIGIN='nl-debug'):
            self.run_command([('ru-1.example', 443, True, '')])

        self.assertEqual(
            MirrorEndpointLiveness.objects.get(host='ru-1.example', port=443).probed_from,
            'nl-debug')

    def test_an_unset_probe_origin_defaults_to_bot(self):
        self.run_command([('ru-1.example', 443, True, '')])

        self.assertEqual(
            MirrorEndpointLiveness.objects.get(host='ru-1.example', port=443).probed_from,
            'bot')

    def test_an_explicitly_empty_probe_origin_stamps_an_empty_string(self):
        with override_settings(SUBSCRIPTION_BACKUP_LIVENESS_PROBE_ORIGIN=''):
            self.run_command([('ru-1.example', 443, True, '')])

        self.assertEqual(
            MirrorEndpointLiveness.objects.get(host='ru-1.example', port=443).probed_from, '')

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


@override_settings(SPECIAL_MONITOR_XRAY_PATH='/bin/true')
class PruneStaleMirrorEndpointLivenessTests(TestCase):
    """A host dropped from the provider's document must stop haunting the table.

    checked_at is only ever refreshed alongside a host being seen in the
    document this run, so its age already means "still offered, as of the
    last time anyone looked" — that is what makes an aged-out row safe to
    delete without a separate "last seen" field.
    """

    def verdict(self, host, *, alive=True, port=443, age_seconds=0):
        MirrorEndpointLiveness.objects.create(
            host=host, port=port, alive=alive,
            checked_at=timezone.now() - datetime.timedelta(seconds=age_seconds))

    def run_command(self, results, **options):
        with patch.object(Command, '_targets', return_value=[ENDPOINT] * len(results)), \
                patch.object(Command, '_probe_one', side_effect=results):
            output = io.StringIO()
            call_command('probe_mirror_liveness', stdout=output, **options)
        return output.getvalue()

    def test_a_successful_run_prunes_only_the_orphan_past_the_threshold(self):
        self.verdict('fresh.example', alive=True, age_seconds=0)
        self.verdict('stale-in-range.example', alive=False, age_seconds=12 * 3600)
        self.verdict('stale-orphan.example', alive=True, age_seconds=30 * 3600)

        output = self.run_command([('fresh.example', 443, True, '')])

        self.assertIn('pruned=1', output)
        remaining = set(MirrorEndpointLiveness.objects.values_list('host', flat=True))
        self.assertEqual(remaining, {'fresh.example', 'stale-in-range.example'})

    def test_the_summary_line_reflects_the_table_after_pruning(self):
        self.verdict('fresh.example', alive=True, age_seconds=0)
        self.verdict('stale-in-range.example', alive=False, age_seconds=12 * 3600)
        self.verdict('stale-orphan.example', alive=True, age_seconds=30 * 3600)

        output = self.run_command([('fresh.example', 443, True, '')])

        self.assertIn('current=2 alive=1', output)

    def test_an_empty_target_list_prunes_nothing(self):
        """The early return for no source makes pruning physically unreachable here."""
        self.verdict('stale-orphan.example', alive=True, age_seconds=30 * 3600)

        with override_settings(SUBSCRIPTION_BACKUP_UPSTREAM_URLS=[]):
            output = io.StringIO()
            call_command('probe_mirror_liveness', stdout=output)

        self.assertNotIn('pruned=', output.getvalue())
        self.assertEqual(MirrorEndpointLiveness.objects.count(), 1)

    def test_nobody_answering_prunes_nothing(self):
        """The early return for no live endpoint also sits before pruning."""
        self.verdict('stale-orphan.example', alive=True, age_seconds=30 * 3600)

        output = self.run_command([('ru-1.example', 443, False, 'timeout'),
                                   ('ru-2.example', 443, False, 'ConnectionResetError')])

        self.assertNotIn('pruned=', output)
        self.assertEqual(MirrorEndpointLiveness.objects.count(), 1)


class PruneStaleMirrorEndpointLivenessMigrationTests(TestCase):
    """The one-off migration must apply exactly the rule the prober now runs forever."""

    def test_the_migration_deletes_only_rows_past_the_shared_threshold(self):
        import importlib

        from django.apps import apps as global_apps

        migration_module = importlib.import_module(
            'apps.subscriptions.migrations.0006_prune_stale_mirror_endpoint_liveness')

        MirrorEndpointLiveness.objects.create(
            host='fresh.example', port=443, alive=True, checked_at=timezone.now())
        MirrorEndpointLiveness.objects.create(
            host='stale.example', port=443, alive=True,
            checked_at=timezone.now() - datetime.timedelta(
                seconds=migration_module.PRUNE_AFTER_SECONDS + 60))

        migration_module.prune_stale_verdicts(global_apps, None)

        remaining = set(MirrorEndpointLiveness.objects.values_list('host', flat=True))
        self.assertEqual(remaining, {'fresh.example'})
