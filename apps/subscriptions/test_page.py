import base64
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import quote

from django.test import RequestFactory, SimpleTestCase, override_settings

from apps.subscriptions import page, views


_URL = 'https://sub.example.test/sub/abcdef0123456789'


class AudienceTests(SimpleTestCase):
    """Кто получает страницу, а кто документ.

    Ошибка в эту сторону дороже обычной: приложение, получившее HTML вместо
    подписки, показывает пустой список — ту самую аварию «серверы пропали».
    """

    def setUp(self):
        self.factory = RequestFactory()

    def test_browser_asks_for_html(self):
        request = self.factory.get('/sub/x', HTTP_ACCEPT='text/html,application/xhtml+xml')

        self.assertTrue(page.wants_page(request))

    def test_client_apps_never_ask_for_html(self):
        for accept in ('*/*', 'application/json', ''):
            request = self.factory.get('/sub/x', HTTP_ACCEPT=accept)

            self.assertFalse(page.wants_page(request), accept)

    def test_missing_accept_header_gets_the_document(self):
        request = self.factory.get('/sub/x')
        request.META.pop('HTTP_ACCEPT', None)

        self.assertFalse(page.wants_page(request))


class AppLinkTests(SimpleTestCase):
    """Схемы взяты из app-config.json remnawave/subscription-page.

    Подстановка у приложений разная, и перепутанная кодировка выглядит как
    рабочая кнопка, которая молча ничего не импортирует.
    """

    def links(self):
        return {name: target for name, target, _ in page.app_links(_URL)}

    def test_happ_takes_the_raw_url(self):
        self.assertEqual(self.links()['Happ'], f'happ://add/{_URL}')

    def test_v2rayng_takes_a_percent_encoded_url(self):
        self.assertEqual(
            self.links()['v2rayNG'],
            f'v2rayng://install-config?name=SPECIAL&url={quote(_URL, safe="")}',
        )

    def test_shadowrocket_takes_base64(self):
        self.assertEqual(
            self.links()['Shadowrocket'],
            'sub://' + base64.b64encode(_URL.encode()).decode(),
        )

    def test_every_button_carries_the_subscription(self):
        for name, target, _ in page.app_links(_URL):
            encoded = quote(_URL, safe='')
            b64 = base64.b64encode(_URL.encode()).decode()
            self.assertTrue(
                _URL in target or encoded in target or b64 in target, name)


class EndpointLabelTests(SimpleTestCase):
    def test_labels_come_from_the_links_the_client_receives(self):
        links = [
            'vless://u@127.0.0.1:1?x=1#' + quote('📊 Подписка-осталось 5 дней'),
            'vless://u@a.test:443?x=1#' + quote('🇳🇱 Нидерланды'),
            'vless://u@b.test:443?x=1#' + quote('🇩🇪 Германия'),
        ]

        self.assertEqual(page.endpoint_labels(links), ['🇳🇱 Нидерланды', '🇩🇪 Германия'])

    def test_status_entry_is_not_offered_as_a_country(self):
        links = ['vless://u@127.0.0.1:1?x=1#' + quote('📊 Подписка-подписка окончена')]

        self.assertEqual(page.endpoint_labels(links), [])


@override_settings(SUBSCRIPTION_PROFILE_TITLE='SPECIAL VPN',
                   SUBSCRIPTION_SUPPORT_URL='https://t.me/support',
                   SUBSCRIPTION_ANNOUNCE_TEXT='')
class RenderTests(SimpleTestCase):
    def render(self, **kwargs):
        defaults = dict(
            subscription_url=_URL,
            days=5,
            status_label='осталось 5 дней',
            links=['vless://u@a.test:443?x=1#' + quote('🇳🇱 Нидерланды')],
            devices=[SimpleNamespace(device_model='iPhone 17 Pro Max', device_os='ios')],
            device_limit=2,
        )
        defaults.update(kwargs)
        return page.render(**defaults)

    def test_page_shows_the_countries_and_the_devices(self):
        html = self.render()

        self.assertIn('🇳🇱 Нидерланды', html)
        self.assertIn('iPhone 17 Pro Max', html)
        self.assertIn('Устройства (1 из 2)', html)

    def test_expired_subscription_reads_the_status_words_not_a_negative_term(self):
        html = self.render(days=0, status_label='подписка окончена')

        self.assertIn('подписка окончена', html)
        self.assertNotIn('осталось 0', html)

    def test_search_engines_are_told_to_stay_away(self):
        """Страница содержит данные доступа: её нельзя индексировать, а
        referrer не должен унести ссылку на чужой домен."""
        html = self.render()

        self.assertIn('name="robots" content="noindex,nofollow"', html)
        self.assertIn('name="referrer" content="no-referrer"', html)

    def test_device_names_are_escaped(self):
        """Название устройства приходит из заголовка клиента, то есть снаружи."""
        html = self.render(devices=[SimpleNamespace(
            device_model='<script>alert(1)</script>', device_os='')])

        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_announcement_is_shown_only_when_it_exists(self):
        with override_settings(SUBSCRIPTION_ANNOUNCE_TEXT=''):
            self.assertNotIn('class="announce"', self.render())
        with override_settings(SUBSCRIPTION_ANNOUNCE_TEXT='Профилактика в 03:00'):
            self.assertIn('Профилактика в 03:00', self.render())



@override_settings(
    SUBSCRIPTION_BASE_URL='https://sub.example.test/sub',
    SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=False,
    SUBSCRIPTION_INTERNAL_INBOUNDS_ENABLED=False,
    SUBSCRIPTION_XRAY_JSON_ENABLED=False,
    SUBSCRIPTION_GRPC_ENABLED=False,
    SUBSCRIPTION_XHTTP_ENABLED=False,
    SUBSCRIPTION_PROFILE_TITLE='SPECIAL VPN',
)
@patch('apps.subscriptions.views.bound_devices', return_value=[])
@patch('apps.subscriptions.views._get_params', return_value={
    'public_key': 'synthetic-public-key', 'server_name': 'sni.example',
    'short_ids': ['synthetic-short-id'], 'port': 8443, 'network': 'tcp',
})
class EndpointAudienceTests(SimpleTestCase):
    """Один адрес обслуживает и человека, и приложение."""

    def _response(self, **headers):
        subscription = SimpleNamespace(
            id=1, enabled=True, sub_id='abcdef0123456789', device_limit=2,
            server=SimpleNamespace(id=1, inbound_id=5, client_vpn_host='relay.example:443',
                                   tariff=SimpleNamespace(price='7.00')),
            user_id=1, vpn_uuid='synthetic-local-id',
        )
        with patch('apps.subscriptions.views.UserVPN.objects') as user_vpn_objects, \
                patch('apps.subscriptions.views.TelegramUser.objects') as telegram_user_objects:
            user_vpn_objects.select_related.return_value.get.return_value = subscription
            telegram_user_objects.annotate_balance.return_value.filter.return_value.first.return_value = (
                SimpleNamespace(balance='70.00'))
            request = RequestFactory().get('/sub/abcdef0123456789', **headers)
            return views.subscription_proxy(request, 'abcdef0123456789')

    def test_browser_gets_a_page_carrying_its_own_subscription_url(self, _params, _devices):
        response = self._response(HTTP_ACCEPT='text/html,application/xhtml+xml')

        self.assertEqual(response['Content-Type'], 'text/html; charset=utf-8')
        self.assertIn(b'https://sub.example.test/sub/abcdef0123456789', response.content)

    def test_client_app_still_gets_the_base64_document(self, _params, _devices):
        response = self._response(HTTP_USER_AGENT='v2rayNG/1.8.0', HTTP_ACCEPT='*/*')

        self.assertEqual(response['Content-Type'], 'text/plain')
        self.assertIn('vless://', base64.b64decode(response.content).decode())

    def test_the_page_is_never_stored(self, _params, _devices):
        """Страница содержит данные доступа, поэтому кэшировать её нельзя."""
        response = self._response(HTTP_ACCEPT='text/html')

        self.assertEqual(response['Cache-Control'], 'private, no-store')

    def test_a_disabled_subscription_gets_the_same_404_as_an_unknown_one(self, _params, _devices):
        subscription = SimpleNamespace(id=1, enabled=False, sub_id='abcdef0123456789')
        with patch('apps.subscriptions.views.UserVPN.objects') as user_vpn_objects:
            user_vpn_objects.select_related.return_value.get.return_value = subscription
            response = views.subscription_proxy(
                RequestFactory().get('/sub/x', HTTP_ACCEPT='text/html'), 'x')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content, b'')