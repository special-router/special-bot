import base64

from django.test import SimpleTestCase

from apps.subscriptions.views import _sanitize_upstream_payload


class RawHysteriaUpstreamTests(SimpleTestCase):
    def test_keeps_vless_and_hy2_lines_from_a_plain_subscription(self):
        payload = (
            b'vless://11111111-2222-3333-4444-555555555555@example.com:443?'
            b'security=reality&sni=cover.example&pbk=synthetic-public-key#vless\n'
            b'hy2://secret@example.com:443/?sni=example.com#hy2\n'
        )

        self.assertEqual(_sanitize_upstream_payload(payload), [
            'vless://11111111-2222-3333-4444-555555555555@example.com:443?'
            'security=reality&sni=cover.example&pbk=synthetic-public-key#vless',
            'hy2://secret@example.com:443/?sni=example.com#hy2',
        ])

    def test_keeps_hysteria2_alias_and_base64_framing(self):
        line = b'hysteria2://secret@example.com:443/?sni=example.com#hy2'

        self.assertEqual(_sanitize_upstream_payload(base64.b64encode(line)), [line.decode()])

    def test_rejects_hy2_without_host_port_auth_or_sni(self):
        payload = b'\n'.join((
            b'hy2://@example.com:443/?sni=example.com',
            b'hy2://secret@:443/?sni=example.com',
            b'hy2://secret@example.com/?sni=example.com',
            b'hy2://secret@example.com:443/',
            b'hy2://secret@example.com:443/?sni=bad host',
        ))

        self.assertEqual(_sanitize_upstream_payload(payload), [])

    def test_rejects_non_proxy_schemes(self):
        self.assertEqual(
            _sanitize_upstream_payload(b'https://example.com\nss://secret@example.com:443'),
            [],
        )

    def test_rejects_insecure_flags_and_duplicate_query_fields(self):
        payload = b'\n'.join((
            b'hy2://secret@example.com:443/?sni=example.com&insecure=1',
            b'hy2://secret@example.com:443/?sni=example.com&SNI=other.example',
        ))

        self.assertEqual(_sanitize_upstream_payload(payload), [])
