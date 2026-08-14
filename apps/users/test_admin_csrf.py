from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings


ORIGIN = 'https://sub.special-wifi.ru'


class AdminLoginBehindTlsTests(TestCase):
    """Вход в админку идёт через nginx на другом хосте.

    До gunicorn запрос доезжает обычным HTTP с `X-Forwarded-Proto: https`, а
    браузер шлёт `Origin: https://…`. Запросы здесь собираются именно так —
    без `secure=True`, которым тестовый клиент объявил бы соединение защищённым
    сам и спрятал бы ровно ту разницу, из-за которой прод отвечал 403.

    403 снимает `CSRF_TRUSTED_ORIGINS`: без него источник не совпадает ни с
    чем доверенным. `SECURE_PROXY_SSL_HEADER` эту проверку не решает — он
    нужен, чтобы `request.is_secure()` говорил правду о TLS, который
    терминировали до нас.
    """

    def setUp(self):
        User.objects.create_superuser(username='operator', password='pw-for-tests-only')
        self.client = Client(enforce_csrf_checks=True)

    def _login(self, origin=ORIGIN):
        page = self.client.get('/admin/login/', HTTP_X_FORWARDED_PROTO='https')
        return self.client.post(
            '/admin/login/',
            {
                'username': 'operator',
                'password': 'pw-for-tests-only',
                'csrfmiddlewaretoken': page.cookies['csrftoken'].value,
                'next': '/admin/',
            },
            HTTP_X_FORWARDED_PROTO='https',
            HTTP_ORIGIN=origin,
            HTTP_REFERER=f'{origin}/admin/login/',
        )

    def test_the_configuration_names_the_proxy_and_the_origin(self):
        self.assertEqual(settings.SECURE_PROXY_SSL_HEADER, ('HTTP_X_FORWARDED_PROTO', 'https'))
        self.assertIn(ORIGIN, settings.CSRF_TRUSTED_ORIGINS)

    def test_a_login_from_the_tls_origin_is_not_refused_as_csrf(self):
        response = self._login()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/admin/')

    @override_settings(CSRF_TRUSTED_ORIGINS=[])
    def test_without_the_trusted_origin_the_login_is_the_403_production_served(self):
        """Тест, который не падает без правки, ничего не охраняет."""
        self.assertEqual(self._login().status_code, 403)

    def test_a_login_from_a_foreign_origin_is_still_refused(self):
        """Доверие к схеме не должно превратиться в доверие к чужому источнику."""
        self.assertEqual(self._login(origin='https://attacker.example').status_code, 403)

    def test_the_proxy_header_is_what_makes_the_request_report_itself_as_secure(self):
        response = self.client.get('/admin/login/', HTTP_X_FORWARDED_PROTO='https')
        self.assertTrue(response.wsgi_request.is_secure())

        with override_settings(SECURE_PROXY_SSL_HEADER=None):
            plain = self.client.get('/admin/login/', HTTP_X_FORWARDED_PROTO='https')
            self.assertFalse(plain.wsgi_request.is_secure())
