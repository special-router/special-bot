from urllib.parse import parse_qs, urlsplit

from apps.subscriptions.views import _build_vless
from django.test import SimpleTestCase


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
