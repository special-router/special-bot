from django.test import SimpleTestCase

from utils.py3xui.async_api import AsyncApi


class PanelTransportTests(SimpleTestCase):
    """The panel moved behind nginx TLS; plaintext access must fail closed."""

    def test_rejects_plaintext_panel_url(self):
        with self.assertRaisesMessage(ValueError, 'xui_https_required'):
            AsyncApi('http://panel.invalid:23133/base', 'user', 'secret')

    def test_rejects_url_without_host(self):
        with self.assertRaisesMessage(ValueError, 'xui_https_required'):
            AsyncApi('/base', 'user', 'secret')

    def test_rejects_disabled_certificate_verification(self):
        with self.assertRaisesMessage(ValueError, 'xui_tls_verification_required'):
            AsyncApi('https://panel.invalid/base', 'user', 'secret', use_tls_verify=False)

    def test_accepts_verified_https_panel_url(self):
        api = AsyncApi('https://panel.invalid/base', 'user', 'secret')

        self.assertIsNotNone(api.inbound)
        self.assertIsNotNone(api.client)
