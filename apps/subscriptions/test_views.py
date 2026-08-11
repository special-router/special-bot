from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from apps.subscriptions.views import _build_vless, _is_backup_test_user, _backup_links
from django.test import SimpleTestCase, override_settings


class BuildVlessTests(SimpleTestCase):
    params = {
        'public_key': 'public-key',
        'server_name': 'example.com',
        'short_ids': ['short-id'],
    }

    def test_legacy_link_omits_flow(self):
        link = _build_vless('client-id', 'vpn.example.com', 8443, 'Direct', self.params)

        query = parse_qs(urlsplit(link).query)

        self.assertNotIn('flow', query)

    def test_explicit_vision_link_keeps_flow(self):
        link = _build_vless(
            'client-id',
            'vpn.example.com',
            8443,
            'Direct',
            self.params,
            flow='xtls-rprx-vision',
        )

        query = parse_qs(urlsplit(link).query)

        self.assertEqual(query['flow'], ['xtls-rprx-vision'])


class BuildVlessNetworkTests(SimpleTestCase):
    params_tcp = {
        'public_key': 'pk', 'server_name': 'sni.example', 'short_ids': ['sid'],
        'network': 'tcp',
    }
    params_grpc = {
        'public_key': 'pk', 'server_name': 'sni.example', 'short_ids': ['sid'],
        'network': 'grpc',
    }

    def test_tcp_link_uses_tcp_type(self):
        link = _build_vless('uuid', 'h', 8443, 'r', self.params_tcp)
        self.assertEqual(parse_qs(urlsplit(link).query)['type'], ['tcp'])

    def test_grpc_link_uses_grpc_type(self):
        link = _build_vless('uuid', 'h', 8080, 'r', self.params_grpc)
        self.assertEqual(parse_qs(urlsplit(link).query)['type'], ['grpc'])


class BackupGateTests(SimpleTestCase):
    @override_settings(SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=False)
    def test_flag_off_excludes_everyone(self):
        self.assertFalse(_is_backup_test_user(1))

    @override_settings(
        SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=True,
        SUBSCRIPTION_BACKUP_TEST_USER_IDS=[],
    )
    def test_empty_allowlist_excludes_everyone(self):
        self.assertFalse(_is_backup_test_user(1))

    @override_settings(
        SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=True,
        SUBSCRIPTION_BACKUP_TEST_USER_IDS=[5, 9],
    )
    def test_allowlist_includes_only_listed(self):
        self.assertTrue(_is_backup_test_user(5))
        self.assertFalse(_is_backup_test_user(6))


class BackupLinksTests(SimpleTestCase):
    _ep = {
        'label': 'PL MORI', 'host': 'logarka.ru', 'port': 443,
        'uuid': 'd3cf9ffc-9faf-4abf-b552-4692085a6378',
        'type': 'tcp', 'security': 'reality', 'pbk': 'pubkey',
        'sni': 'api.logarka.ru', 'sid': 'shortid', 'flow': '',
    }

    @override_settings(
        SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=False,
        SUBSCRIPTION_BACKUP_ENDPOINTS=[_ep],
    )
    def test_flag_off_renders_none(self):
        self.assertIsNone(_backup_links(1, 'uuid'))

    @override_settings(
        SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=True,
        SUBSCRIPTION_BACKUP_ENDPOINTS=[],
    )
    def test_empty_endpoints_renders_none(self):
        self.assertIsNone(_backup_links(1, 'uuid'))

    @override_settings(
        SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=True,
        SUBSCRIPTION_BACKUP_ENDPOINTS=[_ep],
    )
    def test_backup_link_uses_external_uuid_and_host(self):
        links = _backup_links(1, 'ignored-uuid')
        self.assertEqual(len(links), 1)
        parts = urlsplit(links[0])
        self.assertEqual(parts.hostname, 'logarka.ru')
        self.assertEqual(parts.port, 443)
        self.assertEqual(parts.username, 'd3cf9ffc-9faf-4abf-b552-4692085a6378')
        self.assertEqual(parse_qs(parts.query)['sni'], ['api.logarka.ru'])
        self.assertEqual(parse_qs(parts.query)['pbk'], ['pubkey'])
        from urllib.parse import unquote
        self.assertEqual(unquote(parts.fragment), 'PL MORI')
