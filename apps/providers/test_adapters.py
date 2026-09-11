import base64
import json
import os
import tempfile
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.providers.adapters import (
    AServiceAdapter,
    ProviderConfigurationError,
    ProviderTemporaryError,
    VPNStarAdapter,
)
from apps.providers.http import ProviderHTTPResponse, _read_response, fetch_https
from apps.providers.sources import ProviderSource, load_provider_source


_UUID = '11111111-2222-3333-4444-555555555555'


def _source(*, provider_id='a-service', adapter='a-service', kind='subscription'):
    host = 'feed.example'
    return ProviderSource(
        provider_id=provider_id,
        adapter=adapter,
        kind=kind,
        url=f'https://{host}/sub/private-bearer',
        host=host,
        follow_hosts=frozenset({'subscription.example'}),
        user_agent='SPECIAL-provider-ingest/1',
        subscription_user_agent='v2rayNG/1.8.5',
        authorization='Bearer synthetic-api-token' if kind == 'connection_link' else '',
    )


def _vless_lines():
    return [
        (
            f'vless://{_UUID}@tcp-edge.example:443?encryption=none&flow=xtls-rprx-vision'
            '&type=tcp&security=reality&sni=cover.example&fp=chrome&pbk=public-key&sid=abcd#🇳🇱 NL'
        ),
        (
            f'vless://{_UUID}@grpc-edge.example:443?encryption=none&type=grpc&security=reality'
            '&sni=cover.example&fp=chrome&pbk=public-key&serviceName=service&mode=multi#🇩🇪 DE'
        ),
        (
            f'vless://{_UUID}@xhttp-edge.example:443?encryption=none&type=xhttp&security=tls'
            '&sni=xhttp-edge.example&host=cdn.example&path=%2Fapi&mode=auto&alpn=h2#🇫🇮 FI'
        ),
    ]


class AServiceAdapterTests(SimpleTestCase):
    @patch('apps.providers.adapters.builtin.load_provider_source')
    @patch('apps.providers.adapters.builtin.fetch_https')
    def test_normalizes_plain_or_base64_vless_without_storing_uuid(self, fetch_https, load_source):
        load_source.return_value = _source()
        payload = base64.b64encode('\n'.join(_vless_lines()).encode('utf-8'))
        fetch_https.return_value = ProviderHTTPResponse(headers={}, body=payload)

        inventory = AServiceAdapter(SimpleNamespace(slug='a-service')).fetch_inventory()

        self.assertEqual(len(inventory.endpoints), 3)
        self.assertEqual({endpoint.transport for endpoint in inventory.endpoints}, {'tcp', 'grpc', 'xhttp'})
        grpc = next(endpoint for endpoint in inventory.endpoints if endpoint.transport == 'grpc')
        xhttp = next(endpoint for endpoint in inventory.endpoints if endpoint.transport == 'xhttp')
        tcp = next(endpoint for endpoint in inventory.endpoints if endpoint.transport == 'tcp')
        self.assertEqual((grpc.service_name, grpc.transport_mode, grpc.region), ('service', 'multi', 'de'))
        self.assertEqual((xhttp.path, xhttp.host_header, xhttp.alpn), ('/api', 'cdn.example', 'h2'))
        self.assertEqual(tcp.flow, 'xtls-rprx-vision')
        self.assertEqual(fetch_https.call_args.kwargs['user_agent'], 'v2rayNG/1.8.5')
        self.assertNotIn(_UUID, json.dumps([asdict(endpoint) for endpoint in inventory.endpoints]))
        self.assertNotIn('private-bearer', inventory.source_digest)

    @patch('apps.providers.adapters.builtin.load_provider_source')
    @patch('apps.providers.adapters.builtin.fetch_https')
    def test_rejects_inventory_containing_only_status_placeholders(self, fetch_https, load_source):
        load_source.return_value = _source()
        placeholders = '\n'.join((
            f'vless://{_UUID}@status.example:1?type=tcp&security=none#Subscription active',
            f'vless://{_UUID}@status.example:1?type=tcp&security=none#Traffic left',
        )).encode('utf-8')
        fetch_https.return_value = ProviderHTTPResponse(headers={}, body=placeholders)

        with self.assertRaisesMessage(ProviderConfigurationError, 'provider_inventory_empty'):
            AServiceAdapter(SimpleNamespace(slug='a-service')).fetch_inventory()

    @patch('apps.providers.adapters.builtin.load_provider_source')
    @patch('apps.providers.adapters.builtin.fetch_https')
    def test_accepts_base64url_without_padding(self, fetch_https, load_source):
        load_source.return_value = _source()
        payload = base64.urlsafe_b64encode(_vless_lines()[0].encode('utf-8')).rstrip(b'=')
        fetch_https.return_value = ProviderHTTPResponse(headers={}, body=payload)

        inventory = AServiceAdapter(SimpleNamespace(slug='a-service')).fetch_inventory()

        self.assertEqual(len(inventory.endpoints), 1)


class VPNStarAdapterTests(SimpleTestCase):
    @patch('apps.providers.adapters.builtin.load_provider_source')
    @patch('apps.providers.adapters.builtin.fetch_https')
    def test_reads_connection_link_json_then_fetches_subscription(self, fetch_https, load_source):
        source = _source(provider_id='vpnstar', adapter='vpnstar', kind='connection_link')
        load_source.return_value = source
        connection_document = json.dumps({
            'connect_mode': 'HAPP_CRYPT',
            'subscription_url': 'https://subscription.example/private-subscription',
            'happ_scheme_link': 'happ://sub/ignored',
        }).encode('utf-8')
        fetch_https.side_effect = (
            ProviderHTTPResponse(headers={}, body=connection_document),
            ProviderHTTPResponse(headers={}, body='\n'.join(_vless_lines()).encode('utf-8')),
        )

        inventory = VPNStarAdapter(SimpleNamespace(slug='vpnstar')).fetch_inventory()

        self.assertEqual(len(inventory.endpoints), 3)
        self.assertEqual(fetch_https.call_count, 2)
        first, second = fetch_https.call_args_list
        self.assertEqual(first.kwargs['authorization'], 'Bearer synthetic-api-token')
        self.assertEqual(first.kwargs['user_agent'], 'SPECIAL-provider-ingest/1')
        self.assertEqual(second.kwargs['authorization'], '')
        self.assertEqual(second.kwargs['user_agent'], 'v2rayNG/1.8.5')
        self.assertEqual(second.args[0], 'https://subscription.example/private-subscription')
        self.assertIn('subscription.example', second.kwargs['allowed_hosts'])

    @patch('apps.providers.adapters.builtin.load_provider_source')
    @patch('apps.providers.adapters.builtin.fetch_https')
    def test_direct_subscription_mode_does_not_expect_json_envelope(self, fetch_https, load_source):
        load_source.return_value = _source(provider_id='vpnstar', adapter='vpnstar')
        fetch_https.return_value = ProviderHTTPResponse(
            headers={},
            body=base64.b64encode(_vless_lines()[0].encode('utf-8')),
        )

        inventory = VPNStarAdapter(SimpleNamespace(slug='vpnstar')).fetch_inventory()

        self.assertEqual(len(inventory.endpoints), 1)
        self.assertEqual(fetch_https.call_count, 1)
        self.assertEqual(fetch_https.call_args.kwargs['user_agent'], 'v2rayNG/1.8.5')

    @patch('apps.providers.adapters.builtin.load_provider_source')
    @patch('apps.providers.adapters.builtin.fetch_https')
    def test_connection_link_refuses_non_https_happ_wrapper(self, fetch_https, load_source):
        load_source.return_value = _source(provider_id='vpnstar', adapter='vpnstar', kind='connection_link')
        fetch_https.return_value = ProviderHTTPResponse(
            headers={},
            body=json.dumps({'happ_scheme_link': 'happ://crypt/opaque'}).encode('utf-8'),
        )

        with self.assertRaisesMessage(ProviderConfigurationError, 'vpnstar_connection_link_invalid'):
            VPNStarAdapter(SimpleNamespace(slug='vpnstar')).fetch_inventory()


class ProviderSourceTests(SimpleTestCase):
    def _manifest(self, mode=0o600):
        handle = tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', delete=False)
        json.dump({
            'providers': [{
                'id': 'a-service',
                'adapter': 'a-service',
                'kind': 'subscription',
                'url': 'https://feed.example/sub/private-bearer',
                'enabled': True,
            }],
        }, handle)
        handle.close()
        os.chmod(handle.name, mode)
        self.addCleanup(lambda: os.path.exists(handle.name) and os.unlink(handle.name))
        return handle.name

    def test_loads_exact_enabled_adapter_from_mode_0600_file(self):
        path = self._manifest()

        with override_settings(PROVIDER_SOURCE_SECRET_FILE=path):
            source = load_provider_source('a-service', 'a-service')

        self.assertEqual(source.host, 'feed.example')
        self.assertEqual(source.kind, 'subscription')

    def test_rejects_secret_file_with_broad_permissions(self):
        path = self._manifest(mode=0o644)

        with override_settings(PROVIDER_SOURCE_SECRET_FILE=path):
            with self.assertRaisesMessage(ProviderConfigurationError, 'provider_secret_file_invalid'):
                load_provider_source('a-service', 'a-service')

    def test_adapter_mismatch_does_not_fall_back_to_another_source(self):
        path = self._manifest()

        with override_settings(PROVIDER_SOURCE_SECRET_FILE=path):
            with self.assertRaisesMessage(ProviderConfigurationError, 'provider_source_not_found'):
                load_provider_source('a-service', 'vpnstar')

    def test_disabled_source_is_valid_but_cannot_be_loaded(self):
        path = self._manifest()
        with open(path, encoding='utf-8') as source_file:
            document = json.load(source_file)
        document['providers'][0]['enabled'] = False
        with open(path, 'w', encoding='utf-8') as source_file:
            json.dump(document, source_file)
        os.chmod(path, 0o600)

        with override_settings(PROVIDER_SOURCE_SECRET_FILE=path):
            with self.assertRaisesMessage(ProviderConfigurationError, 'provider_source_disabled'):
                load_provider_source('a-service', 'a-service')

    def test_disabled_entry_does_not_invalidate_an_enabled_provider(self):
        path = self._manifest()
        with open(path, encoding='utf-8') as source_file:
            document = json.load(source_file)
        document['providers'].insert(0, {
            'id': 'vpnstar',
            'adapter': 'vpnstar',
            'kind': 'subscription',
            'url': 'https://vpnstar.example/sub/secret',
            'enabled': False,
        })
        with open(path, 'w', encoding='utf-8') as source_file:
            json.dump(document, source_file)
        os.chmod(path, 0o600)

        with override_settings(PROVIDER_SOURCE_SECRET_FILE=path):
            source = load_provider_source('a-service', 'a-service')

        self.assertTrue(source.enabled)


class _FakeConnection:
    def __init__(self, chunks):
        self.chunks = iter(chunks)

    def settimeout(self, timeout):
        self.timeout = timeout

    def recv(self, size):
        return next(self.chunks, b'')


class ProviderHTTPTests(SimpleTestCase):
    @patch('apps.providers.http._fetch_from_address')
    @patch('apps.providers.http._resolve_public')
    def test_retries_each_public_address_within_one_fetch(self, resolve_public, fetch_from_address):
        resolve_public.return_value = {'203.0.113.20', '203.0.113.10'}
        response = ProviderHTTPResponse(headers={}, body=b'test')
        fetch_from_address.side_effect = (ProviderTemporaryError('temporary'), response)

        result = fetch_https(
            'https://feed.example/sub',
            allowed_hosts=frozenset({'feed.example'}),
        )

        self.assertEqual(result, response)
        self.assertEqual(
            [item.kwargs['destination'] for item in fetch_from_address.call_args_list],
            ['203.0.113.10', '203.0.113.20'],
        )

    def test_reads_bounded_chunked_response_with_duplicate_non_framing_header(self):
        connection = _FakeConnection([
            (
                b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n'
                b'Set-Cookie: first=1\r\nSet-Cookie: second=2\r\n\r\n'
                b'4\r\ntest\r\n6\r\n-body!\r\n0\r\n\r\n'
            ),
        ])

        status, headers, body = _read_response(
            connection,
            deadline=10**12,
            read_timeout=1,
            max_bytes=32,
        )

        self.assertEqual(status, 200)
        self.assertEqual(headers['set-cookie'], 'first=1')
        self.assertEqual(body, b'test-body!')

    def test_rejects_content_length_plus_chunked(self):
        connection = _FakeConnection([
            (
                b'HTTP/1.1 200 OK\r\nContent-Length: 4\r\n'
                b'Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n'
            ),
        ])

        with self.assertRaisesMessage(ProviderConfigurationError, 'provider_response_framing_invalid'):
            _read_response(
                connection,
                deadline=10**12,
                read_timeout=1,
                max_bytes=32,
            )

    def test_rejects_non_decimal_content_length(self):
        for declared in ('+4', '1_0'):
            with self.subTest(declared=declared):
                connection = _FakeConnection([
                    f'HTTP/1.1 200 OK\r\nContent-Length: {declared}\r\n\r\ntest'.encode('ascii'),
                ])

                with self.assertRaisesMessage(ProviderConfigurationError, 'provider_response_size_invalid'):
                    _read_response(
                        connection,
                        deadline=10**12,
                        read_timeout=1,
                        max_bytes=32,
                    )

    def test_rejects_non_hexadecimal_chunk_size(self):
        connection = _FakeConnection([
            b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n+4\r\ntest\r\n0\r\n\r\n',
        ])

        with self.assertRaisesMessage(ProviderConfigurationError, 'provider_response_framing_invalid'):
            _read_response(
                connection,
                deadline=10**12,
                read_timeout=1,
                max_bytes=32,
            )
