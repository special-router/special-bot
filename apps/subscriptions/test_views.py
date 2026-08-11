from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from apps.subscriptions.views import _build_vless, _is_mirror_test_user
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


class MirrorGateTests(SimpleTestCase):
    @override_settings(SUBSCRIPTION_MIRROR_INBOUNDS_ENABLED=False)
    def test_flag_off_excludes_everyone(self):
        self.assertFalse(_is_mirror_test_user(1))

    @override_settings(
        SUBSCRIPTION_MIRROR_INBOUNDS_ENABLED=True,
        SUBSCRIPTION_MIRROR_TEST_USER_IDS=[],
    )
    def test_empty_allowlist_excludes_everyone(self):
        self.assertFalse(_is_mirror_test_user(1))

    @override_settings(
        SUBSCRIPTION_MIRROR_INBOUNDS_ENABLED=True,
        SUBSCRIPTION_MIRROR_TEST_USER_IDS=[5, 9],
    )
    def test_allowlist_includes_only_listed(self):
        self.assertTrue(_is_mirror_test_user(5))
        self.assertFalse(_is_mirror_test_user(6))
