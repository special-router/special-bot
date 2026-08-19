"""XHTTP-линия подписки: когда она рендерится и что в ней написано."""
from urllib.parse import parse_qs, unquote, urlsplit

from django.test import SimpleTestCase, override_settings

from apps.subscriptions.catalog import _catalog_from_labels
from apps.subscriptions.views import _ALT_TRANSPORT_LABEL_SUFFIX, _xhttp_link


_HOST = 'sub.example.test'
_UUID = '11111111-2222-3333-4444-555555555555'


@override_settings(SUBSCRIPTION_XHTTP_ENABLED=True, SUBSCRIPTION_XHTTP_PATH='/assets/v1/abcdef',
                   SUBSCRIPTION_XHTTP_PORT=443)
class XhttpLinkTests(SimpleTestCase):
    def test_renders_a_tls_xhttp_uri_on_the_subscription_name(self):
        link = _xhttp_link(_UUID, _HOST)

        self.assertIsNotNone(link)
        parts = urlsplit(link)
        self.assertEqual(parts.scheme, 'vless')
        self.assertEqual(parts.username, _UUID)
        self.assertEqual(parts.hostname, _HOST)
        self.assertEqual(parts.port, 443)

        query = parse_qs(parts.query)
        self.assertEqual(query['type'], ['xhttp'])
        self.assertEqual(query['security'], ['tls'])
        self.assertEqual(query['sni'], [_HOST])
        self.assertEqual(query['path'], ['/assets/v1/abcdef'])
        self.assertEqual(query['mode'], ['auto'])

    def test_label_names_the_exit_country_and_the_fallback_property(self):
        remark = unquote(_xhttp_link(_UUID, _HOST).partition('#')[2])

        self.assertTrue(remark.endswith(_ALT_TRANSPORT_LABEL_SUFFIX))
        self.assertIn('Нидерланды', remark)

    def test_label_does_not_become_a_separate_country_on_the_bot_screen(self):
        remark = unquote(_xhttp_link(_UUID, _HOST).partition('#')[2])

        catalog = _catalog_from_labels(['🇳🇱 Нидерланды', remark])

        self.assertEqual(catalog.countries, ('🇳🇱 Нидерланды',))
        self.assertEqual(catalog.whitelisted, ())

    @override_settings(SUBSCRIPTION_XHTTP_ENABLED=False)
    def test_disabled_renders_nothing(self):
        self.assertIsNone(_xhttp_link(_UUID, _HOST))

    @override_settings(SUBSCRIPTION_XHTTP_PATH='')
    def test_empty_path_renders_nothing(self):
        self.assertIsNone(_xhttp_link(_UUID, _HOST))

    @override_settings(SUBSCRIPTION_XHTTP_PATH='assets/v1/abcdef')
    def test_path_without_leading_slash_renders_nothing(self):
        self.assertIsNone(_xhttp_link(_UUID, _HOST))

    @override_settings(SUBSCRIPTION_XHTTP_PATH='/assets/v1/a b')
    def test_path_with_whitespace_renders_nothing(self):
        self.assertIsNone(_xhttp_link(_UUID, _HOST))

    @override_settings(SUBSCRIPTION_XHTTP_PATH='/assets#frag')
    def test_path_that_would_break_the_uri_renders_nothing(self):
        self.assertIsNone(_xhttp_link(_UUID, _HOST))

    @override_settings(SUBSCRIPTION_XHTTP_PORT=0)
    def test_impossible_port_renders_nothing(self):
        self.assertIsNone(_xhttp_link(_UUID, _HOST))
