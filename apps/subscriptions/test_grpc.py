"""gRPC-линия подписки: когда она рендерится и что в ней написано."""
from urllib.parse import parse_qs, unquote, urlsplit

from django.test import SimpleTestCase, override_settings

from apps.subscriptions.catalog import _catalog_from_labels
from apps.subscriptions.views import _GRPC_LABEL_SUFFIX, _grpc_link, _xray_json_outbounds


_HOST = 'sub.example.test'
_UUID = '11111111-2222-3333-4444-555555555555'
_ENABLED = dict(
    SUBSCRIPTION_GRPC_ENABLED=True,
    SUBSCRIPTION_GRPC_PORT=80,
    SUBSCRIPTION_GRPC_SERVICE_NAME='google',
    SUBSCRIPTION_GRPC_PUBLIC_KEY='xxW4iYaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    SUBSCRIPTION_GRPC_SERVER_NAME='google.com',
    SUBSCRIPTION_GRPC_SHORT_ID='6baeca16fb15cc',
)


@override_settings(**_ENABLED)
class GrpcLinkTests(SimpleTestCase):
    def test_renders_a_reality_grpc_uri_on_the_public_frontend_port(self):
        link = _grpc_link(_UUID, _HOST)

        self.assertIsNotNone(link)
        parts = urlsplit(link)
        self.assertEqual(parts.scheme, 'vless')
        self.assertEqual(parts.username, _UUID)
        self.assertEqual(parts.hostname, _HOST)
        # 80 — фронт nginx, а не 8080, на котором слушает сам inbound.
        self.assertEqual(parts.port, 80)

        query = parse_qs(parts.query)
        self.assertEqual(query['type'], ['grpc'])
        self.assertEqual(query['security'], ['reality'])
        self.assertEqual(query['serviceName'], ['google'])
        self.assertEqual(query['sni'], ['google.com'])
        self.assertEqual(query['sid'], ['6baeca16fb15cc'])
        self.assertEqual(query['fp'], ['chrome'])
        # Клиенты этого inbound-а заведены без flow; навязанный Vision ломает
        # ровно ту линию, ради которой всё и делается.
        self.assertNotIn('flow', query)

    def test_label_names_the_exit_country_and_the_fallback_property(self):
        remark = unquote(_grpc_link(_UUID, _HOST).partition('#')[2])

        self.assertTrue(remark.endswith(_GRPC_LABEL_SUFFIX))
        self.assertIn('Нидерланды', remark)

    def test_label_does_not_become_a_separate_country_on_the_bot_screen(self):
        remark = unquote(_grpc_link(_UUID, _HOST).partition('#')[2])

        catalog = _catalog_from_labels(['🇳🇱 Нидерланды', remark])

        self.assertEqual(catalog.countries, ('🇳🇱 Нидерланды',))
        self.assertEqual(catalog.whitelisted, ())

    def test_xray_json_branch_carries_the_same_endpoint(self):
        params = {
            'public_key': 'p' * 43,
            'server_name': 'example.test',
            'short_ids': ['aabb'],
            'port': 443,
        }
        outbounds = _xray_json_outbounds(_UUID, params, _HOST, 443, '', 0, '')

        grpc = [outbound for outbound in outbounds if outbound['tag'] == 'proxy-grpc']
        self.assertEqual(len(grpc), 1)
        stream = grpc[0]['streamSettings']
        self.assertEqual(stream['network'], 'grpc')
        self.assertEqual(stream['security'], 'reality')
        self.assertEqual(stream['grpcSettings']['serviceName'], 'google')
        self.assertEqual(stream['realitySettings']['serverName'], 'google.com')
        self.assertEqual(grpc[0]['settings']['vnext'][0]['port'], 80)
        # Тег начинается с ``proxy`` — иначе балансировщик его не выберет и
        # линия существовала бы только на бумаге.
        self.assertTrue(grpc[0]['tag'].startswith('proxy'))

    @override_settings(SUBSCRIPTION_GRPC_ENABLED=False)
    def test_disabled_renders_nothing(self):
        self.assertIsNone(_grpc_link(_UUID, _HOST))

    @override_settings(SUBSCRIPTION_GRPC_SERVICE_NAME='')
    def test_missing_service_name_renders_nothing(self):
        self.assertIsNone(_grpc_link(_UUID, _HOST))

    @override_settings(SUBSCRIPTION_GRPC_PUBLIC_KEY='')
    def test_missing_public_key_renders_nothing(self):
        self.assertIsNone(_grpc_link(_UUID, _HOST))

    @override_settings(SUBSCRIPTION_GRPC_SERVER_NAME='')
    def test_missing_server_name_renders_nothing(self):
        self.assertIsNone(_grpc_link(_UUID, _HOST))

    @override_settings(SUBSCRIPTION_GRPC_SHORT_ID='6baeca16fb15c')
    def test_odd_length_short_id_renders_nothing(self):
        self.assertIsNone(_grpc_link(_UUID, _HOST))

    @override_settings(SUBSCRIPTION_GRPC_SHORT_ID='zzzz')
    def test_non_hex_short_id_renders_nothing(self):
        self.assertIsNone(_grpc_link(_UUID, _HOST))

    @override_settings(SUBSCRIPTION_GRPC_SERVICE_NAME='goo gle')
    def test_value_that_would_break_the_uri_renders_nothing(self):
        self.assertIsNone(_grpc_link(_UUID, _HOST))

    @override_settings(SUBSCRIPTION_GRPC_PORT=0)
    def test_impossible_port_renders_nothing(self):
        self.assertIsNone(_grpc_link(_UUID, _HOST))


class GrpcDisabledByDefaultTests(SimpleTestCase):
    def test_unconfigured_deployment_renders_nothing(self):
        """Пустая конфигурация не должна выдавать строку в мёртвый транспорт."""
        self.assertIsNone(_grpc_link(_UUID, _HOST))
