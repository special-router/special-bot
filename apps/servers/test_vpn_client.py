from unittest import TestCase

from apps.servers.vpn_client import _client_endpoint


class ClientEndpointTests(TestCase):
    def test_endpoint_uses_configured_host_port_without_duplication(self):
        self.assertEqual(_client_endpoint('201.34.132.118:443', 443), ('201.34.132.118', 443))

    def test_endpoint_uses_inbound_port_when_host_has_no_port(self):
        self.assertEqual(_client_endpoint('201.34.132.118', 443), ('201.34.132.118', 443))
