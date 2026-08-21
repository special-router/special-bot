"""Прокси подписки на Remnawave: ссылка клиента та же, срок считаем мы."""
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from django.test import RequestFactory, SimpleTestCase, override_settings

from apps.subscriptions.views import _remnawave_proxy_enabled, _remnawave_upstream


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
