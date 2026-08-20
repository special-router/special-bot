"""Вторая ступень зеркальной страны: hysteria2 отдельной строкой."""
from urllib.parse import parse_qs, unquote, urlsplit

from django.test import SimpleTestCase, override_settings

from apps.subscriptions.catalog import _catalog_from_labels
from apps.subscriptions.views import _ALT_TRANSPORT_LABEL_SUFFIX, _mirror_hysteria_link


_ENDPOINT = {
    'host': '13.143.214.1',
    'port': 443,
    'uuid': '11111111-2222-3333-4444-555555555555',
    'remark': '🇳🇴 Норвегия',
    'server_name': 'cloudrynth.com',
    'security': 'reality',
}


@override_settings(SUBSCRIPTION_BACKUP_HYSTERIA_PORT=25443)
class MirrorHysteriaLinkTests(SimpleTestCase):
    def test_renders_hysteria2_on_the_configured_port(self):
        link = _mirror_hysteria_link(_ENDPOINT)

        self.assertIsNotNone(link)
        parts = urlsplit(link)
        self.assertEqual(parts.scheme, 'hy2')
        self.assertEqual(parts.username, _ENDPOINT['uuid'])
        self.assertEqual(parts.hostname, '13.143.214.1')
        # Порт берётся из настройки, а не из vless-строки той же страны: это
        # другой слушатель, а не тот же самый на другом протоколе.
        self.assertEqual(parts.port, 25443)

        query = parse_qs(parts.query)
        self.assertEqual(query['sni'], ['cloudrynth.com'])
        self.assertEqual(query['alpn'], ['h3'])

    def test_label_keeps_the_country_and_marks_it_as_the_second_try(self):
        remark = unquote(_mirror_hysteria_link(_ENDPOINT).partition('#')[2])

        self.assertTrue(remark.startswith('🇳🇴 Норвегия'))
        self.assertTrue(remark.endswith(_ALT_TRANSPORT_LABEL_SUFFIX))

    def test_does_not_become_a_second_country_on_the_bot_screen(self):
        remark = unquote(_mirror_hysteria_link(_ENDPOINT).partition('#')[2])

        catalog = _catalog_from_labels(['🇳🇴 Норвегия', remark])

        self.assertEqual(catalog.countries, ('🇳🇴 Норвегия',))
        self.assertEqual(catalog.whitelisted, ())

    def test_endpoint_without_a_server_name_renders_nothing(self):
        """Reality/TLS без имени сервера не поднимется — строка была бы мёртвой."""
        self.assertIsNone(_mirror_hysteria_link({**_ENDPOINT, 'server_name': ''}))

    def test_endpoint_without_a_uuid_renders_nothing(self):
        self.assertIsNone(_mirror_hysteria_link({**_ENDPOINT, 'uuid': None}))

    def test_value_that_would_break_the_uri_renders_nothing(self):
        self.assertIsNone(_mirror_hysteria_link({**_ENDPOINT, 'host': '13.143.214.1/x'}))


class MirrorHysteriaDisabledTests(SimpleTestCase):
    def test_unconfigured_port_renders_nothing(self):
        """Второй источник того же формата отвечает на другом порту или не отвечает."""
        self.assertIsNone(_mirror_hysteria_link(_ENDPOINT))

    @override_settings(SUBSCRIPTION_BACKUP_HYSTERIA_PORT=70000)
    def test_impossible_port_renders_nothing(self):
        self.assertIsNone(_mirror_hysteria_link(_ENDPOINT))


@override_settings(SUBSCRIPTION_BACKUP_HYSTERIA_PORT=25443)
class MirrorHysteriaFingerprintTests(SimpleTestCase):
    def test_fingerprint_follows_the_endpoint_not_a_default(self):
        """Провайдер объявляет узлы под firefox; chrome — другой хендшейк на проводе."""
        link = _mirror_hysteria_link({**_ENDPOINT, 'fingerprint': 'firefox'})

        self.assertEqual(parse_qs(urlsplit(link).query)['fp'], ['firefox'])

    def test_endpoint_without_a_fingerprint_omits_the_field(self):
        link = _mirror_hysteria_link({**_ENDPOINT, 'fingerprint': ''})

        self.assertNotIn('fp', parse_qs(urlsplit(link).query))
