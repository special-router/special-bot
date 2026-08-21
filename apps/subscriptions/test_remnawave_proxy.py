"""Прокси подписки на Remnawave: ссылка клиента та же, срок считаем мы."""
import base64
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from django.test import RequestFactory, SimpleTestCase, override_settings

from apps.subscriptions.views import (
    _configured_params, _panel_links, _panel_outbounds, _remnawave_proxy_enabled,
    _remnawave_upstream, logger as views_logger)


_REALITY = dict(
    REMNAWAVE_ENABLED=True,
    REMNAWAVE_REALITY_PUBLIC_KEY='p' * 43,
    REMNAWAVE_REALITY_SERVER_NAME='example.test',
    REMNAWAVE_REALITY_SHORT_ID='aabb',
    REMNAWAVE_REALITY_PORT=8443,
)


class ConfiguredParamsTests(SimpleTestCase):
    """Подписка должна собираться, когда 3x-ui уже выключен."""

    @override_settings(**_REALITY)
    def test_params_come_from_settings_without_asking_any_panel(self):
        params = _configured_params(5)

        self.assertEqual(params['public_key'], 'p' * 43)
        self.assertEqual(params['short_ids'], ['aabb'])
        # Порт ядра, а не тот, что набирает клиент: наружу 443 отдаёт nginx.
        self.assertEqual(params['port'], 8443)

    @override_settings(REMNAWAVE_ENABLED=False, **{k: v for k, v in _REALITY.items()
                                                   if k != 'REMNAWAVE_ENABLED'})
    def test_old_panel_stays_authoritative_until_the_switch(self):
        self.assertIsNone(_configured_params(5))

    @override_settings(REMNAWAVE_ENABLED=True, REMNAWAVE_REALITY_PUBLIC_KEY='',
                       REMNAWAVE_REALITY_SERVER_NAME='', REMNAWAVE_REALITY_SHORT_ID='')
    def test_incomplete_settings_fall_back_instead_of_issuing_a_dead_link(self):
        """Ссылка с пустым ключом выглядит рабочей и не подключается."""
        self.assertIsNone(_configured_params(5))


_PROXY = dict(REMNAWAVE_SUBSCRIPTION_PROXY_ENABLED=True,
              REMNAWAVE_SUBSCRIPTION_BASE_URL='https://panel.test/api/sub')


def _user_vpn():
    return SimpleNamespace(sub_id='a' * 32)


def _response(body=b'payload', status=200, headers=None):
    return httpx.Response(status, content=body, headers=headers or {},
                          request=httpx.Request('GET', 'https://panel.test/api/sub/x'))


class GateTests(SimpleTestCase):
    @override_settings(REMNAWAVE_SUBSCRIPTION_PROXY_ENABLED=False,
                       REMNAWAVE_SUBSCRIPTION_BASE_URL='https://panel.test/api/sub')
    def test_flag_off_means_no_request_leaves_the_process(self):
        self.assertFalse(_remnawave_proxy_enabled())

    @override_settings(REMNAWAVE_SUBSCRIPTION_PROXY_ENABLED=True,
                       REMNAWAVE_SUBSCRIPTION_BASE_URL='')
    def test_flag_on_without_an_address_is_still_off(self):
        """Иначе включённый флаг с пустым адресом бьёт запросом в никуда на каждом обновлении."""
        self.assertFalse(_remnawave_proxy_enabled())


@override_settings(**_PROXY)
class UpstreamTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _get(self, **headers):
        return self.factory.get('/sub/x', **headers)

    def test_client_identity_headers_reach_the_panel(self):
        """По ним панель выбирает шаблон приложения и считает устройства."""
        captured = {}

        def fake_get(url, headers=None, **kwargs):
            captured.update(headers or {})
            return _response()

        request = self._get(HTTP_USER_AGENT='Happ/2.9.0', HTTP_X_HWID='device-1',
                            HTTP_X_DEVICE_OS='ios', HTTP_X_DEVICE_MODEL='iPhone')
        with patch.object(httpx, 'get', fake_get):
            _remnawave_upstream(request, _user_vpn())

        self.assertEqual(captured['User-Agent'], 'Happ/2.9.0')
        self.assertEqual(captured['x-hwid'], 'device-1')
        self.assertEqual(captured['x-device-os'], 'ios')

    def test_panel_headers_about_the_term_are_dropped(self):
        """Срок считает бот по балансу; два числа в одном ответе — два разных экрана."""
        # Заголовки ходят по проводу в ASCII: текст объявления уезжает в base64.
        headers = {'subscription-userinfo': 'expire=999', 'profile-title': 'Remnawave',
                   'announce': 'base64:0YfRg9C20L7QtQ==', 'profile-update-interval': '7'}

        with patch.object(httpx, 'get', lambda *a, **k: _response(headers=headers)):
            _, _, passthrough = _remnawave_upstream(self._get(), _user_vpn())

        lowered = {name.lower() for name in passthrough}
        self.assertNotIn('subscription-userinfo', lowered)
        self.assertNotIn('profile-title', lowered)
        self.assertNotIn('announce', lowered)
        # Всё остальное панель знает лучше нас и проходит насквозь.
        self.assertIn('profile-update-interval', lowered)

    def test_length_headers_never_pass_through(self):
        """Content-Length от апстрима переживает подмену тела и рвёт ответ."""
        with patch.object(httpx, 'get',
                          lambda *a, **k: _response(headers={'content-length': '7'})):
            _, _, passthrough = _remnawave_upstream(self._get(), _user_vpn())

        self.assertNotIn('content-length', {name.lower() for name in passthrough})

    def test_a_dead_panel_returns_nothing_instead_of_an_empty_subscription(self):
        """Пустой ответ клиент читает как «серверы пропали» — авария 2026-08-20."""
        def fail(*args, **kwargs):
            raise httpx.ConnectError('down')

        with patch.object(httpx, 'get', fail):
            self.assertIsNone(_remnawave_upstream(self._get(), _user_vpn()))

    def test_non_200_is_refused_rather_than_forwarded(self):
        with patch.object(httpx, 'get', lambda *a, **k: _response(b'', status=404)):
            self.assertIsNone(_remnawave_upstream(self._get(), _user_vpn()))

    def test_redirects_are_not_followed(self):
        """Иначе ответ панели уводил бы запрос на адрес, которого мы не задавали."""
        captured = {}

        def fake_get(url, headers=None, **kwargs):
            captured.update(kwargs)
            return _response()

        with patch.object(httpx, 'get', fake_get):
            _remnawave_upstream(self._get(), _user_vpn())

        self.assertFalse(captured['follow_redirects'])


_ENDPOINTS = dict(REMNAWAVE_ENDPOINTS_ENABLED=True,
                  REMNAWAVE_ENDPOINTS_ALL_USERS_ENABLED=True,
                  REMNAWAVE_API_URL='https://panel.test')
_UUID = '11111111-2222-3333-4444-555555555555'
_OTHER = '99999999-2222-3333-4444-555555555555'


def _panel_user(user_vpn_id=801):
    return SimpleNamespace(id=user_vpn_id, sub_id='b' * 32, vpn_uuid=_UUID)


def _panel_body(*links):
    return base64.b64encode('\n'.join(links).encode()).decode()


_DIRECT = (f'vless://{_UUID}@sub.test:443?type=tcp&security=reality&sni=a.test'
           f'&pbk={"p" * 43}&sid=aabb&fp=chrome#NL')
_RELAY = (f'vless://{_UUID}@203.0.113.9:443?type=tcp&security=reality&sni=a.test'
          f'&pbk={"p" * 43}&sid=aabb&fp=chrome#RU')
_XHTTP = (f'vless://{_UUID}@sub.test:443?type=xhttp&security=tls&sni=sub.test'
          f'&path=%2Fassets%2Fv1%2Fx&fp=chrome#XHTTP')
_GRPC = (f'vless://{_UUID}@sub.test:80?type=grpc&security=reality&sni=g.test'
         f'&pbk={"p" * 43}&sid=aabb&serviceName=svc#GRPC')


class PanelEndpointGateTests(SimpleTestCase):
    """Флаг решает раньше сети: выключенный не должен стоить ни одного запроса."""

    @override_settings(REMNAWAVE_ENDPOINTS_ENABLED=False, REMNAWAVE_API_URL='https://panel.test')
    def test_flag_off_asks_nothing(self):
        def fail(*args, **kwargs):
            raise AssertionError('panel must not be queried')

        with patch.object(httpx, 'get', fail):
            self.assertIsNone(_panel_links(_panel_user()))

    @override_settings(REMNAWAVE_ENDPOINTS_ENABLED=True, REMNAWAVE_API_URL='')
    def test_flag_on_without_an_address_is_still_off(self):
        self.assertIsNone(_panel_links(_panel_user()))

    @override_settings(REMNAWAVE_ENDPOINTS_ENABLED=True, REMNAWAVE_API_URL='https://panel.test',
                       REMNAWAVE_ENDPOINTS_TEST_USER_IDS=[801])
    def test_only_the_listed_account_is_served_from_the_panel(self):
        calls = []

        def record(*args, **kwargs):
            calls.append(args)
            return _response(_panel_body(_DIRECT).encode())

        with patch.object(httpx, 'get', record):
            listed = _panel_links(_panel_user(801))
            other = _panel_links(_panel_user(802))

        self.assertEqual(listed, [_DIRECT])
        self.assertIsNone(other)
        self.assertEqual(len(calls), 1, 'an unlisted account must not reach the panel')

    @override_settings(REMNAWAVE_ENDPOINTS_ENABLED=True, REMNAWAVE_API_URL='https://panel.test',
                       REMNAWAVE_ENDPOINTS_TEST_USER_IDS=[])
    def test_an_empty_allowlist_serves_nobody(self):
        """Иначе поднятый флаг с пустым списком раскатывался бы на всех разом."""
        self.assertIsNone(_panel_links(_panel_user(801)))

    @override_settings(REMNAWAVE_ENDPOINTS_ENABLED=True, REMNAWAVE_API_URL='https://panel.test',
                       REMNAWAVE_ENDPOINTS_TEST_USER_IDS='801')
    def test_a_malformed_allowlist_serves_nobody(self):
        self.assertIsNone(_panel_links(_panel_user(801)))


@override_settings(**_ENDPOINTS)
class PanelLinksTests(SimpleTestCase):
    def test_panel_lines_are_taken_as_they_are(self):
        body = _panel_body(_DIRECT, _RELAY, _XHTTP, _GRPC)
        with patch.object(httpx, 'get', lambda *a, **k: _response(body.encode())):
            links = _panel_links(_panel_user())

        self.assertEqual(links, [_DIRECT, _RELAY, _XHTTP, _GRPC])

    def test_lines_for_another_identity_are_dropped(self):
        """Чужой UUID в нашем ответе — это выданный чужой доступ, а не деградация."""
        foreign = _DIRECT.replace(_UUID, _OTHER)
        body = _panel_body(_DIRECT, foreign)
        with patch.object(httpx, 'get', lambda *a, **k: _response(body.encode())):
            links = _panel_links(_panel_user())

        self.assertEqual(links, [_DIRECT])

    def test_a_panel_without_hosts_falls_back_instead_of_emptying_the_list(self):
        """Пустой список клиент читает как «серверы пропали» — авария 2026-08-20."""
        with patch.object(httpx, 'get', lambda *a, **k: _response(_panel_body().encode())):
            self.assertIsNone(_panel_links(_panel_user()))

    def test_a_dead_panel_falls_back_to_the_built_links(self):
        def fail(*args, **kwargs):
            raise httpx.ConnectError('down')

        with patch.object(httpx, 'get', fail):
            self.assertIsNone(_panel_links(_panel_user()))

    def test_non_200_is_refused(self):
        with patch.object(httpx, 'get', lambda *a, **k: _response(b'', status=404)):
            self.assertIsNone(_panel_links(_panel_user()))

    def test_the_subscription_id_never_reaches_the_log(self):
        """Ссылка подписки — данные доступа: в логе её быть не может."""
        def fail(*args, **kwargs):
            raise httpx.ConnectError('down')

        with patch.object(httpx, 'get', fail), self.assertLogs(views_logger, 'WARNING') as logs:
            _panel_links(_panel_user())

        self.assertNotIn('b' * 32, '\n'.join(logs.output))


class PanelOutboundTests(SimpleTestCase):
    """Профиль и список обязаны нести одни точки, иначе они расходятся у клиента."""

    def test_every_transport_lands_on_the_tag_the_routing_names(self):
        outbounds = _panel_outbounds([_DIRECT, _RELAY, _XHTTP, _GRPC], 'sub.test')

        self.assertEqual([o['tag'] for o in outbounds],
                         ['proxy-nl-direct', 'proxy-ru-relay', 'proxy-xhttp', 'proxy-grpc'])

    def test_the_customer_identity_is_carried_into_the_document(self):
        outbounds = _panel_outbounds([_DIRECT], 'sub.test')

        self.assertEqual(outbounds[0]['settings']['vnext'][0]['users'][0]['id'], _UUID)

    def test_a_transport_the_ladder_cannot_place_is_omitted(self):
        """Точка без правила маршрутизации видна клиенту и не используется."""
        unknown = _DIRECT.replace('type=tcp', 'type=kcp')

        self.assertEqual(_panel_outbounds([unknown], 'sub.test'), [])

    def test_a_second_line_on_the_same_stage_does_not_shadow_the_first(self):
        twin = _XHTTP.replace('%2Fassets%2Fv1%2Fx', '%2Fassets%2Fv1%2Fy')
        outbounds = _panel_outbounds([_XHTTP, twin], 'sub.test')

        self.assertEqual(len(outbounds), 1)
        self.assertEqual(outbounds[0]['streamSettings']['xhttpSettings']['path'],
                         '/assets/v1/x')
