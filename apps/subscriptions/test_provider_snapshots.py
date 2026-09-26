import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.subscriptions import provider_snapshots, views
from apps.subscriptions.router import build_router_config


LINE_A = ('vless://11111111-2222-3333-4444-555555555555@192.0.2.10:443?'
          'type=tcp&security=reality&sni=example.test&pbk=public-key&sid=abcdef01#%F0%9F%87%A9%F0%9F%87%AA+Germany')
LINE_B = ('vless://aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee@192.0.2.11:443?'
          'type=tcp&security=reality&sni=example.test&pbk=public-key&sid=abcdef01#%F0%9F%87%AF%F0%9F%87%B5+Japan')
LINE_C = ('vless://12345678-1234-1234-1234-123456789abc@192.0.2.12:443?'
          'type=tcp&security=reality&sni=example.test&pbk=public-key&sid=abcdef01#%F0%9F%87%AB%F0%9F%87%B7+France')
MANIFEST = [
    {'id': 'a-service', 'adapter': 'subscription', 'host': 'a.example', 'enabled': True},
    {'id': 'vpnstar', 'adapter': 'subscription', 'host': 'b.example', 'enabled': True},
]


class ProviderSnapshotTests(SimpleTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings = override_settings(
            SUBSCRIPTION_PROVIDER_SNAPSHOTS_ENABLED=True,
            SUBSCRIPTION_PROVIDER_SNAPSHOT_DIR=self.directory.name,
            SUBSCRIPTION_PROVIDER_SNAPSHOT_MAX_AGE_SECONDS=86400,
            SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=True,
            SUBSCRIPTION_BACKUP_PROVIDER_MANIFEST=MANIFEST,
            SUBSCRIPTION_BACKUP_UPSTREAM_URLS=['https://a.example/sub/a', 'https://b.example/sub/b'],
            SUBSCRIPTION_BACKUP_MAX_MIRROR_ENTRIES=16,
        )
        self.settings.enable()
        self.addCleanup(self.settings.disable)

    def test_public_reader_uses_complete_verified_snapshot_without_network(self):
        provider_snapshots._publish('a-service', [LINE_A])
        provider_snapshots._publish('vpnstar', [LINE_B])
        provider_snapshots._publish_scope(('a-service', 'vpnstar'))
        with override_settings(
            SUBSCRIPTION_BACKUP_PROVIDER_MANIFEST=[],
            SUBSCRIPTION_BACKUP_UPSTREAM_URLS=[],
        ), patch.object(views, '_cached_upstream_links') as network:
            self.assertEqual(views._backup_links(), [LINE_A, LINE_B])
        network.assert_not_called()

    def test_refresh_failure_retains_previous_pointer(self):
        provider_snapshots._publish('a-service', [LINE_A])
        before = (Path(self.directory.name) / 'current-a-service.json').read_bytes()
        with patch.object(views, '_fetch_upstream_payload', side_effect=OSError('secret detail')):
            result = provider_snapshots.refresh_provider_snapshots()
        self.assertFalse(result['a-service']['ok'])
        self.assertNotIn('secret detail', json.dumps(result))
        self.assertEqual((Path(self.directory.name) / 'current-a-service.json').read_bytes(), before)

    def test_public_reader_keeps_ready_provider_when_another_is_unavailable(self):
        provider_snapshots._publish('vpnstar', [LINE_B])
        provider_snapshots._publish_scope(('a-service', 'vpnstar'))

        self.assertEqual(views._backup_links(), [LINE_B])

    def test_three_sources_keep_independent_last_known_good_on_refresh_failure(self):
        manifest = MANIFEST + [
            {'id': 'lunaire', 'adapter': 'subscription', 'host': 'c.example', 'enabled': True},
        ]
        with override_settings(
            SUBSCRIPTION_BACKUP_PROVIDER_MANIFEST=manifest,
            SUBSCRIPTION_BACKUP_UPSTREAM_URLS=[
                'https://a.example/sub/a', 'https://b.example/sub/b', 'https://c.example/sub/c',
            ],
            SUBSCRIPTION_BACKUP_MAX_MIRROR_ENTRIES=192,
        ):
            provider_snapshots._publish('a-service', [LINE_A])
            provider_snapshots._publish('vpnstar', [LINE_B])
            provider_snapshots._publish('lunaire', [LINE_C])
            provider_snapshots._publish_scope(('a-service', 'vpnstar', 'lunaire'))
            before = (Path(self.directory.name) / 'current-lunaire.json').read_bytes()

            def fetch(url, *, user_agent):
                if url.endswith('/c'):
                    raise OSError('provider bearer detail')
                return {}, b'a' if url.endswith('/a') else b'b'

            with patch.object(views, '_fetch_upstream_payload', side_effect=fetch), \
                    patch.object(views, '_sanitize_upstream_payload',
                                 side_effect=lambda payload, headers: [LINE_A if payload == b'a' else LINE_B]):
                result = provider_snapshots.refresh_provider_snapshots()

            self.assertEqual([result[key]['ok'] for key in ('a-service', 'vpnstar', 'lunaire')],
                             [True, True, False])
            self.assertNotIn('provider bearer detail', json.dumps(result))
            self.assertEqual((Path(self.directory.name) / 'current-lunaire.json').read_bytes(), before)
            self.assertEqual(views._backup_links(), [LINE_A, LINE_B, LINE_C])
            router = build_router_config()
            self.assertEqual(router['outbounds'][0]['tag'], 'GLOBAL AUTO')
            self.assertEqual(len(router['outbounds'][0]['outbounds']), 3)

    def test_public_reader_removes_snapshot_endpoint_with_fresh_dead_verdict(self):
        provider_snapshots._publish('a-service', [LINE_A])
        provider_snapshots._publish('vpnstar', [LINE_B])
        provider_snapshots._publish_scope(('a-service', 'vpnstar'))

        with patch.object(views, '_liveness_verdicts', return_value={
            ('192.0.2.10', 443): False,
            ('192.0.2.11', 443): True,
        }):
            self.assertEqual(views._backup_links(), [LINE_B])

    def test_router_snapshot_has_global_auto_first_and_no_direct(self):
        provider_snapshots._publish('a-service', [LINE_A])
        provider_snapshots._publish('vpnstar', [LINE_B])
        provider_snapshots._publish_scope(('a-service', 'vpnstar'))
        config = build_router_config()
        self.assertEqual(config['outbounds'][0]['tag'], 'GLOBAL AUTO')
        self.assertEqual(config['outbounds'][0]['type'], 'urltest')
        self.assertNotIn('direct', {item['type'] for item in config['outbounds']})
        self.assertEqual(config['route']['final'], 'GLOBAL AUTO')

    def test_router_snapshot_omits_xray_only_xhttp_transport(self):
        xhttp = LINE_A.replace('type=tcp', 'type=xhttp')
        provider_snapshots._publish('a-service', [xhttp, LINE_A])
        provider_snapshots._publish_scope(('a-service',))

        config = build_router_config()

        transports = [item.get('transport', {}).get('type') for item in config['outbounds']]
        self.assertNotIn('xhttp', transports)
        self.assertEqual(config['outbounds'][0]['tag'], 'GLOBAL AUTO')

    def test_country_selector_tags_never_collide_with_endpoint_tags(self):
        second_germany = LINE_A.replace('192.0.2.10', '192.0.2.12')
        provider_snapshots._publish('a-service', [LINE_A, second_germany])
        provider_snapshots._publish_scope(('a-service',))

        config = build_router_config()
        tags = [item['tag'] for item in config['outbounds']]

        self.assertEqual(len(tags), len(set(tags)))
        self.assertIn('COUNTRY 🇩🇪Germany', tags)

    def test_tampered_artifact_fails_closed(self):
        provider_snapshots._publish('a-service', [LINE_A])
        pointer = json.loads((Path(self.directory.name) / 'current-a-service.json').read_text())
        artifact = Path(self.directory.name) / pointer['artifact']
        artifact.chmod(0o600)
        artifact.write_bytes(artifact.read_bytes() + b' ')
        self.assertEqual(provider_snapshots.provider_snapshot_lines(), {})
