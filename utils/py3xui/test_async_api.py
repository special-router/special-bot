from unittest import TestCase
from unittest.mock import patch

from utils.py3xui.async_api import AsyncApi


class AsyncApiTlsTests(TestCase):
    def test_http_and_disabled_verification_are_rejected_before_clients(self):
        with self.assertRaisesRegex(ValueError, 'https_required'):
            AsyncApi('http://panel.invalid', 'user', 'password')
        with self.assertRaisesRegex(ValueError, 'verification_required'):
            AsyncApi('https://panel.invalid', 'user', 'password', use_tls_verify=False)

    @patch('utils.py3xui.async_api.AsyncServerApi')
    @patch('utils.py3xui.async_api.AsyncDatabaseApi')
    @patch('utils.py3xui.async_api.AsyncInboundApi')
    @patch('utils.py3xui.async_api.AsyncClientApi')
    def test_protected_ca_setting_is_forwarded_with_verification(self, client, inbound, database, server):
        from django.test import override_settings
        with override_settings(XUI_PANEL_CA_FILE_INVALID=False,
                               XUI_PANEL_CA_CERTIFICATE_PATH='/protected/ca.pem'):
            AsyncApi('https://panel.invalid', 'user', 'password')
        self.assertTrue(client.call_args.args[3])
        self.assertEqual(client.call_args.args[4], '/protected/ca.pem')
