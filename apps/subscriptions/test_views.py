import base64
import copy
import hashlib
import io
import json
import logging
import logging.config
import os
import stat
import sys
import tempfile
import threading
import time
from fnmatch import fnmatchcase
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, unquote, urlsplit

from django.test import RequestFactory, SimpleTestCase, override_settings

from apps.subscriptions import views
from apps.subscriptions.views import _build_vless, _is_backup_test_user, _is_internal_test_user


class BuildVlessTests(SimpleTestCase):
    params = {
        'public_key': 'public-key',
        'server_name': 'example.com',
        'short_ids': ['short-id'],
    }

    def test_legacy_link_omits_flow(self):
        link = _build_vless('client-id', 'vpn.example.com', 8443, 'Direct', self.params)

        query = parse_qs(urlsplit(link).query)

        self.assertNotIn('flow', query)

    def test_explicit_vision_link_keeps_flow(self):
        link = _build_vless(
            'client-id',
            'vpn.example.com',
            8443,
            'Direct',
            self.params,
            flow='xtls-rprx-vision',
        )

        query = parse_qs(urlsplit(link).query)

        self.assertEqual(query['flow'], ['xtls-rprx-vision'])

    def test_legacy_tcp_bytes_are_unchanged_for_deployed_safe_values(self):
        self.assertEqual(
            _build_vless('client-id', 'vpn.example.com', 8443, 'Direct', self.params),
            'vless://client-id@vpn.example.com:8443?type=tcp&security=reality&'
            'pbk=public-key&fp=chrome&sni=example.com&sid=short-id&spx=%2F#Direct',
        )

    def test_query_values_are_percent_encoded(self):
        params = dict(self.params, public_key='key+with space', server_name='sni/name')
        query = parse_qs(urlsplit(_build_vless(
            'client-id', 'vpn.example.com', 8443, 'remark', params)).query)
        self.assertEqual(query['pbk'], ['key+with space'])
        self.assertEqual(query['sni'], ['sni/name'])


class BuildVlessNetworkTests(SimpleTestCase):
    params_tcp = {
        'public_key': 'pk', 'server_name': 'sni.example', 'short_ids': ['sid'],
        'network': 'tcp',
    }
    params_grpc = {
        'public_key': 'pk', 'server_name': 'sni.example', 'short_ids': ['sid'],
        'network': 'grpc',
    }

    def test_tcp_link_uses_tcp_type(self):
        link = _build_vless('client-id', 'host.example', 8443, 'remark', self.params_tcp)
        self.assertEqual(parse_qs(urlsplit(link).query)['type'], ['tcp'])

    def test_grpc_link_uses_grpc_type(self):
        link = _build_vless('client-id', 'host.example', 8080, 'remark', self.params_grpc)
        self.assertEqual(parse_qs(urlsplit(link).query)['type'], ['grpc'])


class InternalInboundCanaryTests(SimpleTestCase):
    endpoints = [
        {'inbound_id': 7, 'advertised_port': 39329, 'label': '🇳🇱 NL TCP 39329'},
        {'inbound_id': 9, 'advertised_port': 46517, 'label': '🇳🇱 NL TCP 46517'},
        {'inbound_id': 13, 'advertised_port': 27914, 'label': '🇳🇱 NL TCP 27914'},
        {'inbound_id': 10, 'advertised_port': 80, 'label': '🇳🇱 NL gRPC 80'},
    ]

    def setUp(self):
        super().setUp()
        with views._INTERNAL_PROFILE_CACHE_LOCK:
            views._INTERNAL_PROFILE_CACHE.clear()
        self.addCleanup(lambda: views._INTERNAL_PROFILE_CACHE.clear())

    @override_settings(SUBSCRIPTION_INTERNAL_INBOUNDS_ENABLED=False)
    def test_disabled_flag_excludes_everyone(self):
        self.assertFalse(_is_internal_test_user(801))

    @override_settings(SUBSCRIPTION_INTERNAL_INBOUNDS_ENABLED=True,
                       SUBSCRIPTION_INTERNAL_TEST_USER_IDS=[],
                       SUBSCRIPTION_INTERNAL_ENDPOINTS=endpoints)
    def test_empty_allowlist_excludes_everyone(self):
        self.assertFalse(_is_internal_test_user(801))

    @override_settings(SUBSCRIPTION_INTERNAL_INBOUNDS_ENABLED=True,
                       SUBSCRIPTION_INTERNAL_TEST_USER_IDS=[801],
                       SUBSCRIPTION_INTERNAL_ENDPOINTS=endpoints)
    def test_only_uservpn_801_is_eligible(self):
        self.assertTrue(_is_internal_test_user(801))
        self.assertFalse(_is_internal_test_user(802))

    @override_settings(SUBSCRIPTION_INTERNAL_INBOUNDS_ENABLED=True,
                       SUBSCRIPTION_INTERNAL_TEST_USER_IDS=[801, 802],
                       SUBSCRIPTION_INTERNAL_ENDPOINTS=endpoints)
    def test_broadened_or_malformed_allowlist_fails_open(self):
        self.assertFalse(_is_internal_test_user(801))

    @override_settings(SUBSCRIPTION_INTERNAL_INBOUNDS_ENABLED=True,
                       SUBSCRIPTION_INTERNAL_TEST_USER_IDS=[801],
                       SUBSCRIPTION_INTERNAL_ENDPOINTS=[
                           {'inbound_id': 8, 'advertised_port': 20057, 'label': 'bad'},
                       ])
    def test_forbidden_inbound_fails_open(self):
        self.assertFalse(_is_internal_test_user(801))

    @override_settings(SUBSCRIPTION_INTERNAL_INBOUNDS_ENABLED=True,
                       SUBSCRIPTION_INTERNAL_TEST_USER_IDS=[801],
                       SUBSCRIPTION_INTERNAL_ENDPOINTS=[
                           {'inbound_id': 10, 'advertised_port': 8080, 'label': 'bad'},
                       ])
    def test_grpc_backend_port_fails_open(self):
        self.assertFalse(_is_internal_test_user(801))

    @override_settings(SUBSCRIPTION_INTERNAL_INBOUNDS_ENABLED=True,
                       SUBSCRIPTION_INTERNAL_TEST_USER_IDS=[801],
                       SUBSCRIPTION_INTERNAL_ENDPOINTS=[
                           {'inbound_id': 7, 'advertised_port': 39329, 'label': 'one'},
                           {'inbound_id': 7, 'advertised_port': 46517, 'label': 'two'},
                       ])
    def test_duplicate_config_fails_open(self):
        self.assertFalse(_is_internal_test_user(801))

    @override_settings(SUBSCRIPTION_INTERNAL_INBOUNDS_ENABLED=True,
                       SUBSCRIPTION_INTERNAL_TEST_USER_IDS=[801],
                       SUBSCRIPTION_INTERNAL_ENDPOINTS=[
                           {'inbound_id': 7, 'advertised_port': 39329, 'label': 'bad\nlabel'},
                       ])
    def test_malformed_label_fails_open(self):
        self.assertFalse(_is_internal_test_user(801))

    def _snapshot(self, inbound_id, *, membership=None, service_name='synthetic-service'):
        port, network, security = views._INTERNAL_EXPECTED[inbound_id]
        return {
            'enabled': True, 'port': port, 'protocol': 'vless', 'network': network,
            'security': security, 'public_key': f'pk-{inbound_id}',
            'server_name': f'sni-{inbound_id}.example', 'short_id': f'sid-{inbound_id}',
            'service_name': service_name if inbound_id == 10 else '',
            'membership': [(True, 0)] if membership is None else membership,
        }

    @override_settings(SUBSCRIPTION_BASE_URL='https://sub.example/sub',
                       SUBSCRIPTION_INTERNAL_INBOUNDS_ENABLED=True,
                       SUBSCRIPTION_INTERNAL_TEST_USER_IDS=[801],
                       SUBSCRIPTION_INTERNAL_ENDPOINTS=endpoints)
    @patch('apps.subscriptions.views._stable_internal_snapshots')
    def test_target_profiles_are_isolated_and_transport_specific(self, stable):
        stable.side_effect = lambda _server, inbounds, _uuid: {
            inbound: self._snapshot(inbound) for inbound in inbounds}

        links = views._internal_links(1, 'synthetic-client')

        self.assertEqual(len(links), 4)
        tcp = [parse_qs(urlsplit(link).query) for link in links[:3]]
        self.assertEqual([query['pbk'] for query in tcp], [['pk-7'], ['pk-9'], ['pk-13']])
        self.assertTrue(all('flow' not in query for query in tcp))
        grpc = parse_qs(urlsplit(links[3]).query)
        self.assertEqual(urlsplit(links[3]).port, 80)
        self.assertEqual(grpc['type'], ['grpc'])
        self.assertEqual(grpc['serviceName'], ['synthetic-service'])
        self.assertNotIn('flow', grpc)

    @override_settings(SUBSCRIPTION_BASE_URL='https://sub.example/sub',
                       SUBSCRIPTION_INTERNAL_INBOUNDS_ENABLED=True,
                       SUBSCRIPTION_INTERNAL_TEST_USER_IDS=[801],
                       SUBSCRIPTION_INTERNAL_ENDPOINTS=endpoints)
    @patch('apps.subscriptions.views._stable_internal_snapshots')
    def test_missing_membership_or_malformed_transport_omits_only_candidate(self, stable):
        def snapshots(_server, inbounds, _uuid):
            result = {inbound: self._snapshot(inbound) for inbound in inbounds}
            result[9]['membership'] = []
            result[13]['network'] = 'ws'
            return result
        stable.side_effect = snapshots

        links = views._internal_links(1, 'synthetic-client')

        self.assertEqual(links, [])

    @override_settings(SUBSCRIPTION_INTERNAL_INBOUNDS_ENABLED=True,
                       SUBSCRIPTION_INTERNAL_TEST_USER_IDS=None,
                       SUBSCRIPTION_INTERNAL_ENDPOINTS=endpoints)
    def test_malformed_allowlist_value_fails_open(self):
        self.assertFalse(_is_internal_test_user(801))

    def test_raw_snapshot_extracts_grpc_service_name_from_live_raw_fields(self):
        inbound = {
            'enable': True, 'port': 8080, 'protocol': 'vless',
            'settings': {'clients': [{'id': 'synthetic', 'enable': True, 'expiryTime': 0}]},
            'streamSettings': {
                'network': 'grpc', 'security': 'reality',
                'grpcSettings': {'serviceName': 'synthetic-service'},
                'realitySettings': {'settings': {'publicKey': 'pk'},
                                    'serverNames': ['sni'], 'shortIds': ['sid']},
            },
        }
        self.assertEqual(
            views._normalized_internal_snapshot(inbound, 'synthetic')['service_name'],
            'synthetic-service',
        )

    def test_raw_snapshot_requires_exactly_one_enabled_unexpired_client(self):
        inbound = {
            'enable': True, 'port': 39329, 'protocol': 'vless',
            'settings': {'clients': [{'id': 'synthetic', 'enable': True, 'expiryTime': 0}]},
            'streamSettings': {
                'network': 'tcp', 'security': 'reality',
                'realitySettings': {'settings': {'publicKey': 'pk'},
                                    'serverNames': ['sni'], 'shortIds': ['sid']},
            },
        }
        snapshot = views._normalized_internal_snapshot(inbound, 'synthetic')
        self.assertEqual(snapshot['membership'], [(True, 0)])
        inbound['settings']['clients'].append(dict(inbound['settings']['clients'][0]))
        self.assertEqual(len(views._normalized_internal_snapshot(inbound, 'synthetic')['membership']), 2)


class BackupGateTests(SimpleTestCase):
    @override_settings(SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=False)
    def test_flag_off_excludes_everyone(self):
        self.assertFalse(_is_backup_test_user(1))

    @override_settings(
        SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=True,
        SUBSCRIPTION_BACKUP_TEST_USER_IDS=[],
    )
    def test_empty_allowlist_excludes_everyone(self):
        self.assertFalse(_is_backup_test_user(1))

    @override_settings(
        SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=True,
        SUBSCRIPTION_BACKUP_TEST_USER_IDS=[5, 9],
    )
    def test_allowlist_includes_only_listed(self):
        self.assertTrue(_is_backup_test_user(5))
        self.assertFalse(_is_backup_test_user(6))

    @override_settings(
        SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=True,
        SUBSCRIPTION_BACKUP_TEST_USER_IDS='not-a-list',
    )
    def test_non_list_allowlist_excludes_everyone(self):
        self.assertFalse(_is_backup_test_user(5))


class LegacySubscriptionTests(SimpleTestCase):
    @override_settings(
        SUBSCRIPTION_BASE_URL='https://direct.example/sub',
        SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=False,
        # Pinned: production advertises 443, and an ambient value would other-
        # wise decide what this test asserts about the default inbound port.
        SUBSCRIPTION_DIRECT_ADVERTISED_PORT=0,
        # Pinned for the same reason: this test *is* the three-line contract,
        # so the setting that retires the status entry must not be ambient.
        SUBSCRIPTION_STATUS_ENTRY_ENABLED=True,
    )
    @patch('apps.subscriptions.views._get_params')
    @patch('apps.subscriptions.views.TelegramUser.objects')
    @patch('apps.subscriptions.views.UserVPN.objects')
    def test_flag_off_keeps_status_direct_relay_and_no_flow_contract(
        self, user_vpn_objects, telegram_user_objects, get_params,
    ):
        user_vpn_objects.select_related.return_value.get.return_value = SimpleNamespace(
            id=1,
            enabled=True,
            server=SimpleNamespace(
                id=1,
                inbound_id=5,
                client_vpn_host='relay.example:443',
                tariff=None,
            ),
            user_id=1,
            vpn_uuid='synthetic-local-id',
        )
        telegram_user_objects.annotate_balance.return_value.filter.return_value.first.return_value = None
        get_params.return_value = {
            'public_key': 'synthetic-public-key',
            'server_name': 'sni.example',
            'short_ids': ['synthetic-short-id'],
            'port': 8443,
            'network': 'tcp',
        }

        response = views.subscription_proxy(RequestFactory().get('/sub/synthetic'), 'synthetic')
        lines = base64.b64decode(response.content).decode().splitlines()

        self.assertEqual(len(lines), 3)
        self.assertEqual(urlsplit(lines[1]).hostname, 'direct.example')
        self.assertEqual(urlsplit(lines[2]).hostname, 'relay.example')
        self.assertNotIn('flow', parse_qs(urlsplit(lines[1]).query))
        self.assertNotIn('flow', parse_qs(urlsplit(lines[2]).query))
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response['Pragma'], 'no-cache')
        self.assertEqual(urlsplit(lines[1]).port, 8443)

    @override_settings(
        SUBSCRIPTION_BASE_URL='https://direct.example/sub',
        SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=False,
        SUBSCRIPTION_DIRECT_ADVERTISED_PORT=443,
        SUBSCRIPTION_STATUS_ENTRY_ENABLED=True,
    )
    @patch('apps.subscriptions.views._get_params')
    @patch('apps.subscriptions.views.TelegramUser.objects')
    @patch('apps.subscriptions.views.UserVPN.objects')
    def test_advertised_direct_port_overrides_private_inbound_port(
        self, user_vpn_objects, telegram_user_objects, get_params,
    ):
        user_vpn_objects.select_related.return_value.get.return_value = SimpleNamespace(
            id=1, enabled=True,
            server=SimpleNamespace(id=1, inbound_id=5, client_vpn_host='relay.example:443', tariff=None),
            user_id=1, vpn_uuid='synthetic-local-id',
        )
        telegram_user_objects.annotate_balance.return_value.filter.return_value.first.return_value = None
        get_params.return_value = {
            'public_key': 'synthetic-public-key', 'server_name': 'sni.example',
            'short_ids': ['synthetic-short-id'], 'port': 8443, 'network': 'tcp',
        }

        response = views.subscription_proxy(RequestFactory().get('/sub/synthetic'), 'synthetic')
        lines = base64.b64decode(response.content).decode().splitlines()

        self.assertEqual(len(lines), 3)
        # Only the direct entry moves to the shared listener; relay is untouched.
        self.assertEqual(urlsplit(lines[1]).hostname, 'direct.example')
        self.assertEqual(urlsplit(lines[1]).port, 443)
        self.assertEqual(urlsplit(lines[2]).hostname, 'relay.example')
        self.assertEqual(urlsplit(lines[2]).port, 443)

    @override_settings(
        SUBSCRIPTION_BASE_URL='https://direct.example/sub',
        SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=True,
        SUBSCRIPTION_BACKUP_TEST_USER_IDS=[1],
        SUBSCRIPTION_BACKUP_UPSTREAM_URLS=['https://subscription.example/unavailable'],
        SUBSCRIPTION_STATUS_ENTRY_ENABLED=True,
    )
    @patch('apps.subscriptions.views._fetch_upstream_payload', side_effect=OSError())
    @patch('apps.subscriptions.views._get_params')
    @patch('apps.subscriptions.views.TelegramUser.objects')
    @patch('apps.subscriptions.views.UserVPN.objects')
    def test_all_failed_backups_keep_legacy_three_line_subscription(
        self, user_vpn_objects, telegram_user_objects, get_params, fetch,
    ):
        user_vpn_objects.select_related.return_value.get.return_value = SimpleNamespace(
            id=1, enabled=True,
            server=SimpleNamespace(id=1, inbound_id=5, client_vpn_host='relay.example:443', tariff=None),
            user_id=1, vpn_uuid='synthetic-local-id',
        )
        telegram_user_objects.annotate_balance.return_value.filter.return_value.first.return_value = None
        get_params.return_value = {
            'public_key': 'synthetic-public-key', 'server_name': 'sni.example',
            'short_ids': ['synthetic-short-id'], 'port': 8443, 'network': 'tcp',
        }

        response = views.subscription_proxy(RequestFactory().get('/sub/synthetic'), 'synthetic')
        lines = base64.b64decode(response.content).decode().splitlines()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(lines), 3)
        fetch.assert_called_once_with('https://subscription.example/unavailable')


@override_settings(
    SUBSCRIPTION_BASE_URL='https://direct.example/sub',
    SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=False,
    SUBSCRIPTION_INTERNAL_INBOUNDS_ENABLED=False,
    SUBSCRIPTION_DIRECT_ADVERTISED_PORT=0,
    SUBSCRIPTION_PROFILE_TITLE='SPECIAL VPN',
    SUBSCRIPTION_SUPPORT_URL='https://support.example/help',
    SUBSCRIPTION_ANNOUNCE_TEXT='',
    BOT_LINK='https://t.me/synthetic_bot',
)
@patch('apps.subscriptions.views._get_params', return_value={
    'public_key': 'synthetic-public-key', 'server_name': 'sni.example',
    'short_ids': ['synthetic-short-id'], 'port': 8443, 'network': 'tcp',
})
class ClientUiHeaderTests(SimpleTestCase):
    """The headers the client app builds its interface from.

    ``expire`` is the valuable one: it is what lets the dead 127.0.0.1 status
    entry be retired, so every boundary that could produce a nonsensical term
    is asserted here rather than left to the client to interpret.
    """

    def _response(self, balance, price='7.00'):
        tariff = SimpleNamespace(price=price) if price is not None else None
        subscription = SimpleNamespace(
            id=1, enabled=True,
            server=SimpleNamespace(id=1, inbound_id=5, client_vpn_host='relay.example:443', tariff=tariff),
            user_id=1, vpn_uuid='synthetic-local-id',
        )
        user = SimpleNamespace(balance=balance) if balance is not None else None
        with patch('apps.subscriptions.views.UserVPN.objects') as user_vpn_objects, \
                patch('apps.subscriptions.views.TelegramUser.objects') as telegram_user_objects:
            user_vpn_objects.select_related.return_value.get.return_value = subscription
            telegram_user_objects.annotate_balance.return_value.filter.return_value.first.return_value = user
            return views.subscription_proxy(RequestFactory().get('/sub/synthetic'), 'synthetic')

    def _expire(self, response):
        fields = dict(
            field.strip().split('=', 1)
            for field in response['subscription-userinfo'].split(';')
        )
        return int(fields['expire'])

    def test_expire_carries_the_same_remaining_days_as_the_status_remark(self, _params):
        response = self._response(balance='70.00')

        remaining = self._expire(response) - int(time.time())

        # Ten funded days, allowing for the second the request itself took.
        self.assertLessEqual(abs(remaining - 10 * 86400), 5)
        status_line = base64.b64decode(response.content).decode().splitlines()[0]
        self.assertIn('осталось 10 дней', unquote(urlsplit(status_line).fragment))

    def test_an_empty_balance_expires_now_rather_than_never(self, _params):
        # expire=0 is how this format spells "unlimited", which is the opposite
        # of what an account with no money should tell the client.
        response = self._response(balance='0.00')

        self.assertNotEqual(self._expire(response), 0)
        self.assertLessEqual(abs(self._expire(response) - int(time.time())), 5)

    def test_an_overdrawn_balance_never_produces_a_term_in_the_past(self, _params):
        response = self._response(balance='-50.00')

        self.assertGreaterEqual(self._expire(response), int(time.time()) - 5)

    def test_a_subscription_without_a_tariff_still_gets_a_usable_expiry(self, _params):
        response = self._response(balance='70.00', price=None)

        self.assertLessEqual(abs(self._expire(response) - int(time.time())), 5)

    def test_an_unknown_user_gets_headers_and_not_an_error(self, _params):
        response = self._response(balance=None)

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(abs(self._expire(response) - int(time.time())), 5)

    def test_display_text_travels_base64_encoded(self, _params):
        response = self._response(balance='70.00')

        self.assertEqual(
            base64.b64decode(response['profile-title'].removeprefix('base64:')).decode(),
            'SPECIAL VPN',
        )
        self.assertEqual(response['support-url'], 'https://support.example/help')
        self.assertEqual(response['profile-web-page-url'], 'https://t.me/synthetic_bot')

    def test_an_unset_announcement_sends_no_banner_header(self, _params):
        self.assertNotIn('announce', self._response(balance='70.00').headers)

    @override_settings(SUBSCRIPTION_ANNOUNCE_TEXT='Профилактика 14 августа с 03:00 до 05:00 МСК.')
    def test_a_configured_announcement_survives_cyrillic(self, _params):
        response = self._response(balance='70.00')

        self.assertEqual(
            base64.b64decode(response['announce'].removeprefix('base64:')).decode(),
            'Профилактика 14 августа с 03:00 до 05:00 МСК.',
        )

    @override_settings(SUBSCRIPTION_SUPPORT_URL='https://support.example/\r\nX-Injected: 1')
    def test_an_unusable_configured_url_drops_its_header_instead_of_the_response(self, _params):
        # Django turns a newline in a header value into BadHeaderError, which
        # would be a 500 on every refresh for one bad environment value.
        response = self._response(balance='70.00')

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('support-url', response.headers)


@override_settings(
    SUBSCRIPTION_BASE_URL='https://direct.example/sub',
    SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=False,
    SUBSCRIPTION_INTERNAL_INBOUNDS_ENABLED=False,
    SUBSCRIPTION_DIRECT_ADVERTISED_PORT=0,
    SUBSCRIPTION_STATUS_ENTRY_ENABLED=False,
)
@patch('apps.subscriptions.views._get_params', return_value={
    'public_key': 'synthetic-public-key', 'server_name': 'sni.example',
    'short_ids': ['synthetic-short-id'], 'port': 8443, 'network': 'tcp',
})
class RetiredStatusEntryTests(SimpleTestCase):
    """What a served subscription looks like once the dead entry is switched off."""

    def _response(self):
        with patch('apps.subscriptions.views.UserVPN.objects') as user_vpn_objects, \
                patch('apps.subscriptions.views.TelegramUser.objects') as telegram_user_objects:
            user_vpn_objects.select_related.return_value.get.return_value = SimpleNamespace(
                id=1, enabled=True,
                server=SimpleNamespace(id=1, inbound_id=5, client_vpn_host='relay.example:443',
                                       tariff=SimpleNamespace(price='7.00')),
                user_id=1, vpn_uuid='synthetic-local-id',
            )
            telegram_user_objects.annotate_balance.return_value.filter.return_value.first.return_value = (
                SimpleNamespace(balance='70.00'))
            return views.subscription_proxy(RequestFactory().get('/sub/synthetic'), 'synthetic')

    def test_only_the_working_endpoints_remain_and_keep_their_bytes(self, _params):
        response = self._response()
        lines = base64.b64decode(response.content).decode().splitlines()

        self.assertEqual(len(lines), 2)
        self.assertEqual(urlsplit(lines[0]).hostname, 'direct.example')
        self.assertEqual(urlsplit(lines[1]).hostname, 'relay.example')
        self.assertNotIn('127.0.0.1', base64.b64decode(response.content).decode())

    def test_the_remaining_term_is_still_served_by_the_header(self, _params):
        response = self._response()

        expire = int(dict(
            field.strip().split('=', 1)
            for field in response['subscription-userinfo'].split(';')
        )['expire'])

        self.assertLessEqual(abs(expire - int(time.time()) - 10 * 86400), 5)


class _FakeTLSSocket:
    def __init__(self, response, peer='8.8.8.8'):
        self.response = response
        self.peer = peer
        self.sent = []
        self.timeouts = []
        self.closed = False

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def getpeername(self):
        return (self.peer, 443)

    def sendall(self, data):
        self.sent.append(data)

    def recv(self, size):
        result, self.response = self.response[:size], self.response[size:]
        return result

    def close(self):
        self.closed = True


class ExternalSubscriptionTests(SimpleTestCase):
    opaque_link = (
        'vless://synthetic-provider-id@backup.example:443?'
        'type=tcp&security=reality&flow=xtls-rprx-vision&fp=firefox&'
        'spx=%2Fedge&unknown-provider-param=a%2Bb#Synthetic%20Backup'
    )

    def setUp(self):
        super().setUp()
        views._clear_backup_cache()
        self.addCleanup(views._clear_backup_cache)

    def test_plain_payload_preserves_opaque_link_byte_for_byte(self):
        payload = (self.opaque_link + '\n').encode()

        self.assertEqual(views._sanitize_upstream_payload(payload), [self.opaque_link])

    def test_standard_base64_payload_preserves_opaque_link_byte_for_byte(self):
        payload = base64.b64encode((self.opaque_link + '\n').encode())

        self.assertEqual(views._sanitize_upstream_payload(payload), [self.opaque_link])

    def test_sentinel_and_unsupported_entries_are_filtered(self):
        usable = self.opaque_link
        payload = '\n'.join((
            'ss://unsupported',
            'vless://synthetic@127.0.0.1:1?type=tcp#status',
            'vless://synthetic@backup.example:443?type=tcp#Expired',
            'vless://synthetic@backup.example:443?type=tcp#Dummy',
            usable,
        )).encode()

        self.assertEqual(views._sanitize_upstream_payload(payload), [usable])

    @override_settings(
        SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=True,
        SUBSCRIPTION_BACKUP_UPSTREAM_URLS=['https://subscription.example/one'],
    )
    @patch('apps.subscriptions.views._fetch_upstream_payload')
    def test_backup_links_fetches_and_preserves_upstream_line(self, fetch):
        fetch.return_value = (self.opaque_link + '\n').encode()

        self.assertEqual(views._backup_links(), [self.opaque_link])
        fetch.assert_called_once_with('https://subscription.example/one')

    @override_settings(
        SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=True,
        SUBSCRIPTION_BACKUP_UPSTREAM_URLS=[
            'https://subscription.example/one',
            'https://subscription.example/two',
        ],
    )
    @patch('apps.subscriptions.views._cached_upstream_links')
    def test_multiple_upstreams_are_aggregated(self, cached_links):
        cached_links.side_effect = [['vless://synthetic-one'], ['vless://synthetic-two']]

        self.assertEqual(
            views._backup_links(),
            ['vless://synthetic-one', 'vless://synthetic-two'],
        )
        self.assertEqual(cached_links.call_count, 2)

    @override_settings(SUBSCRIPTION_BACKUP_CACHE_TTL_SECONDS=60)
    @patch('apps.subscriptions.views._fetch_upstream_payload')
    def test_expired_cache_is_removed_before_failed_refetch(self, fetch):
        url = 'https://subscription.example/cache'
        key = views._backup_cache_key(url)
        with views._BACKUP_CACHE_LOCK:
            views._BACKUP_CACHE[key] = (0, [self.opaque_link])
        fetch.side_effect = OSError()

        self.assertEqual(views._cached_upstream_links(url), [])
        with views._BACKUP_CACHE_LOCK:
            self.assertNotIn(key, views._BACKUP_CACHE)

    @override_settings(
        SUBSCRIPTION_BACKUP_CONNECT_TIMEOUT_SECONDS=2,
        SUBSCRIPTION_BACKUP_READ_TIMEOUT_SECONDS=4,
        SUBSCRIPTION_BACKUP_RESPONSE_MAX_BYTES=5,
    )
    @patch('apps.subscriptions.views._resolve_public_upstream', return_value={'8.8.8.8'})
    @patch('apps.subscriptions.views.ssl.create_default_context')
    @patch('apps.subscriptions.views.socket.create_connection')
    def test_pinned_connection_uses_original_sni_host_and_rejects_oversized_response(
        self, create_connection, create_context, resolve,
    ):
        raw_socket = Mock()
        tls_socket = _FakeTLSSocket(b'HTTP/1.1 200 OK\r\nContent-Length: 6\r\n\r\n123456')
        create_connection.return_value = raw_socket
        create_context.return_value.wrap_socket.return_value = tls_socket

        with self.assertRaisesRegex(ValueError, 'too_large'):
            views._fetch_upstream_payload('https://subscription.example:8443/path?q=1')

        create_connection.assert_called_once_with(('8.8.8.8', 8443), timeout=2.0)
        create_context.return_value.wrap_socket.assert_called_once_with(raw_socket, server_hostname='subscription.example')
        self.assertTrue(tls_socket.closed)

    @override_settings(SUBSCRIPTION_BACKUP_RESPONSE_MAX_BYTES=5)
    @patch('apps.subscriptions.views._resolve_public_upstream', return_value={'8.8.8.8'})
    @patch('apps.subscriptions.views.ssl.create_default_context')
    @patch('apps.subscriptions.views.socket.create_connection')
    def test_fetch_sends_origin_form_path_and_host_to_pinned_ip(self, create_connection, create_context, resolve):
        raw_socket = Mock()
        tls_socket = _FakeTLSSocket(b'HTTP/1.1 200 OK\r\n\r\n1234')
        create_connection.return_value = raw_socket
        create_context.return_value.wrap_socket.return_value = tls_socket

        self.assertEqual(views._fetch_upstream_payload('https://subscription.example:8443/path?q=1'), b'1234')

        self.assertEqual(create_connection.call_args.args[0], ('8.8.8.8', 8443))
        request = b''.join(tls_socket.sent).decode('ascii')
        self.assertIn('GET /path?q=1 HTTP/1.1\r\n', request)
        self.assertIn('Host: subscription.example:8443\r\n', request)
        self.assertNotIn('https://subscription.example', request)
        self.assertTrue(tls_socket.closed)

    @override_settings(SUBSCRIPTION_BACKUP_RESPONSE_MAX_BYTES=5)
    @patch('apps.subscriptions.views._resolve_public_upstream', return_value={'8.8.8.8'})
    @patch('apps.subscriptions.views.ssl.create_default_context')
    @patch('apps.subscriptions.views.socket.create_connection')
    def test_streaming_response_size_cap_is_strict(self, create_connection, create_context, resolve):
        tls_socket = _FakeTLSSocket(b'HTTP/1.1 200 OK\r\n\r\n123456')
        create_connection.return_value = Mock()
        create_context.return_value.wrap_socket.return_value = tls_socket

        with self.assertRaisesRegex(ValueError, 'too_large'):
            views._fetch_upstream_payload('https://subscription.example/size')

        self.assertTrue(tls_socket.closed)

    @override_settings(
        SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=True,
        SUBSCRIPTION_BACKUP_UPSTREAM_URLS=['https://bad.example:bad', 'https://subscription.example/valid'],
    )
    @patch('apps.subscriptions.views._cached_upstream_links', return_value=[opaque_link])
    def test_malformed_url_does_not_block_later_valid_source(self, cached):
        self.assertEqual(views._backup_links(), [self.opaque_link])
        cached.assert_called_once_with('https://subscription.example/valid')

    @override_settings(SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=True, SUBSCRIPTION_BACKUP_UPSTREAM_URLS='invalid')
    def test_non_list_config_fails_open(self):
        self.assertIsNone(views._backup_links())

    def test_non_public_unicast_dns_answers_are_rejected(self):
        for address in ('127.0.0.1', '0.0.0.0', '224.0.0.1', '169.254.1.1', '10.0.0.1', '240.0.0.1'):
            completed = SimpleNamespace(stdout=f'{address} STREAM synthetic.example\n')
            with self.subTest(address=address), patch(
                'apps.subscriptions.views.subprocess.run', return_value=completed,
            ):
                with self.assertRaisesRegex(ValueError, 'unsafe'):
                    views._resolve_public_upstream('https://subscription.example/', time.monotonic() + 1)

    @patch('apps.subscriptions.views.subprocess.run')
    def test_dns_resolver_parses_valid_dual_stack_answers(self, resolver):
        resolver.side_effect = [
            SimpleNamespace(stdout='8.8.8.8 STREAM synthetic.example\n8.8.8.8 DGRAM synthetic.example\n'),
            SimpleNamespace(stdout='2001:4860:4860::8888 STREAM synthetic.example\n'),
        ]

        self.assertEqual(
            views._resolve_public_upstream('https://subscription.example/', time.monotonic() + 1),
            {'8.8.8.8', '2001:4860:4860::8888'},
        )
        self.assertEqual(resolver.call_count, 2)

    def test_stalled_dns_resolver_returns_within_absolute_deadline(self):
        deadline_seconds = 0.1
        started = time.monotonic()
        with patch.object(
            views, '_DNS_RESOLVER_COMMAND',
            (sys.executable, '-c', 'import time; time.sleep(10)'),
        ):
            with self.assertRaisesRegex(ValueError, 'unsafe'):
                views._resolve_public_upstream(
                    'https://subscription.example/', started + deadline_seconds)

        self.assertLess(time.monotonic() - started, 0.5)

    @patch('apps.subscriptions.views._resolve_public_upstream', return_value={'8.8.8.8'})
    @patch('apps.subscriptions.views.ssl.create_default_context')
    @patch('apps.subscriptions.views.socket.create_connection')
    def test_connected_peer_mismatch_is_rejected_before_get(self, create_connection, create_context, resolve):
        tls_socket = _FakeTLSSocket(b'HTTP/1.1 200 OK\r\n\r\n', peer='1.1.1.1')
        create_connection.return_value = Mock()
        create_context.return_value.wrap_socket.return_value = tls_socket

        with self.assertRaisesRegex(ValueError, 'peer_mismatch'):
            views._fetch_upstream_payload('https://subscription.example/')

        self.assertEqual(tls_socket.sent, [])
        self.assertTrue(tls_socket.closed)

    @patch('apps.subscriptions.views._resolve_public_upstream', return_value={'8.8.8.8'})
    @patch('apps.subscriptions.views.ssl.create_default_context')
    @patch('apps.subscriptions.views.socket.create_connection')
    def test_compressed_response_is_rejected(self, create_connection, create_context, resolve):
        tls_socket = _FakeTLSSocket(b'HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\n\r\n')
        create_connection.return_value = Mock()
        create_context.return_value.wrap_socket.return_value = tls_socket

        with self.assertRaisesRegex(ValueError, 'compressed'):
            views._fetch_upstream_payload('https://subscription.example/')

        self.assertTrue(tls_socket.closed)

    @override_settings(SUBSCRIPTION_BACKUP_UPSTREAM_HOSTS=['allowed.example'])
    def test_upstream_url_validation_accepts_dns_and_rejects_unsafe_forms(self):
        self.assertTrue(views._valid_upstream_url('https://allowed.example:8443/path'))
        for value in (
            'http://allowed.example/', 'https://127.0.0.1/',
            'https://user@allowed.example/', 'https://allowed.example/#fragment',
            'https://allowed.example:99999/', 'https://other.example/',
        ):
            self.assertFalse(views._valid_upstream_url(value))

    def test_sentinel_boundary_words_are_preserved(self):
        for marker in ('Unexpired', 'NonDummy'):
            link = self.opaque_link.replace('Synthetic%20Backup', marker)
            self.assertEqual(views._sanitize_upstream_payload((link + '\n').encode()), [link])

    @override_settings(SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=True,
                       SUBSCRIPTION_BACKUP_UPSTREAM_URLS=['https://subscription.example/one', 'https://subscription.example/two'])
    @patch('apps.subscriptions.views._cached_upstream_links')
    def test_aggregate_deduplicates_stably(self, cached):
        cached.side_effect = [[self.opaque_link, 'vless://synthetic@one.example:443#One'], [self.opaque_link]]
        self.assertEqual(views._backup_links(), [self.opaque_link, 'vless://synthetic@one.example:443#One'])

    @override_settings(
        SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=True,
        SUBSCRIPTION_BACKUP_UPSTREAM_URLS=['https://subscription.example/one'],
        SUBSCRIPTION_BACKUP_ALLOWED_LINE_SHA256=None,
    )
    @patch('apps.subscriptions.views._cached_upstream_links')
    def test_absent_line_allowlist_preserves_all_valid_lines(self, cached):
        additional_link = 'vless://synthetic@other.example:443?type=tcp#Other'
        cached.return_value = [self.opaque_link, additional_link]

        self.assertEqual(views._backup_links(), [self.opaque_link, additional_link])

    @override_settings(
        SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=True,
        SUBSCRIPTION_BACKUP_UPSTREAM_URLS=['https://subscription.example/one'],
    )
    @patch('apps.subscriptions.views._cached_upstream_links')
    def test_line_allowlist_selects_exact_opaque_line_without_normalizing_it(self, cached):
        same_query_different_bytes = self.opaque_link.replace('spx=%2Fedge', 'spx=%2fedge')
        same_fragment_different_bytes = self.opaque_link.replace('Synthetic%20Backup', 'Synthetic%20backup')
        allowed_digest = hashlib.sha256(self.opaque_link.encode('utf-8')).hexdigest()
        cached.return_value = [same_query_different_bytes, same_fragment_different_bytes, self.opaque_link]

        with self.settings(SUBSCRIPTION_BACKUP_ALLOWED_LINE_SHA256=[allowed_digest]):
            self.assertEqual(views._backup_links(), [self.opaque_link])

    @override_settings(
        SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=True,
        SUBSCRIPTION_BACKUP_UPSTREAM_URLS=['https://subscription.example/one'],
        SUBSCRIPTION_BACKUP_ALLOWED_LINE_SHA256=['A' * 64],
    )
    @patch('apps.subscriptions.views._cached_upstream_links')
    def test_malformed_line_allowlist_fails_safe(self, cached):
        cached.return_value = [self.opaque_link]

        self.assertIsNone(views._backup_links())
        cached.assert_not_called()

    @override_settings(SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=True,
                       SUBSCRIPTION_BACKUP_UPSTREAM_URLS=['https://subscription.example/one'],
                       SUBSCRIPTION_BACKUP_MAX_SOURCES=0)
    @patch('apps.subscriptions.views._cached_upstream_links', return_value=[opaque_link])
    def test_source_limit_is_bounded_even_for_invalid_setting(self, cached):
        self.assertEqual(views._backup_links(), [self.opaque_link])
        cached.assert_called_once()

    @override_settings(SUBSCRIPTION_BACKUP_FETCH_DEADLINE_SECONDS=1,
                       SUBSCRIPTION_BACKUP_READ_TIMEOUT_SECONDS=30)
    @patch('apps.subscriptions.views._resolve_public_upstream', return_value={'8.8.8.8'})
    @patch('apps.subscriptions.views.ssl.create_default_context')
    @patch('apps.subscriptions.views.socket.create_connection')
    def test_slow_drip_cannot_extend_absolute_deadline(
        self, create_connection, create_context, resolve,
    ):
        class Clock:
            now = 0.0

            def monotonic(self):
                return self.now

        class DripSocket(_FakeTLSSocket):
            def recv(self, _size):
                clock.now += 0.6
                return b'x'

        clock = Clock()
        tls_socket = DripSocket(b'')
        create_connection.return_value = Mock()
        create_context.return_value.wrap_socket.return_value = tls_socket

        with patch('apps.subscriptions.views.time.monotonic', clock.monotonic):
            with self.assertRaisesRegex(ValueError, 'deadline'):
                views._fetch_upstream_payload('https://subscription.example/')

        self.assertGreaterEqual(len(tls_socket.timeouts), 2)
        self.assertTrue(tls_socket.closed)

    @override_settings(SUBSCRIPTION_BACKUP_CACHE_TTL_SECONDS=60)
    @patch('apps.subscriptions.views._fetch_upstream_payload')
    def test_cache_single_flight_and_eviction_are_thread_safe(self, fetch):
        url = 'https://subscription.example/cache'
        payload = (self.opaque_link + '\n').encode()
        entered = threading.Event()
        release = threading.Event()

        def delayed_fetch(_url):
            entered.set()
            release.wait(timeout=2)
            return payload

        fetch.side_effect = delayed_fetch
        results = []
        threads = [threading.Thread(target=lambda: results.append(views._cached_upstream_links(url)))
                   for _ in range(8)]
        for thread in threads:
            thread.start()
        self.assertTrue(entered.wait(timeout=1))
        release.set()
        for thread in threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())

        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(results, [[self.opaque_link]] * 8)
        with views._BACKUP_CACHE_LOCK:
            views._BACKUP_CACHE.update({str(index): (0, []) for index in range(64)})
        workers = [threading.Thread(target=views._evict_backup_cache, args=(set(),)) for _ in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
        with views._BACKUP_CACHE_LOCK:
            self.assertEqual(views._BACKUP_CACHE, {})


class MirrorIngestTests(SimpleTestCase):
    """Ingestion of providers that answer with a JSON document per User-Agent."""

    plaintext_outbound = {
        'type': 'vless',
        'tag': 'Mirror Plain',
        'server': 'mirror-one.example',
        'server_port': 443,
        'uuid': 'synthetic-plain-id',
        'tls': {'enabled': False},
    }
    reality_outbound = {
        'type': 'vless',
        'tag': 'Mirror Reality',
        'server': 'mirror-two.example',
        'server_port': 8443,
        'uuid': 'synthetic-reality-id',
        'flow': 'xtls-rprx-vision',
        'tls': {
            'enabled': True,
            'server_name': 'sni.example',
            'utls': {'fingerprint': 'chrome'},
            'reality': {'enabled': True, 'public_key': 'synthetic-pbk', 'short_id': 'ab01'},
        },
        'transport': {'type': 'grpc', 'service_name': 'synthetic-service'},
    }
    reality_link = (
        'vless://synthetic-reality-id@mirror-two.example:8443?'
        'flow=xtls-rprx-vision&type=grpc&security=reality&pbk=synthetic-pbk&fp=chrome&'
        'sni=sni.example&sid=ab01&spx=%2F&serviceName=synthetic-service#Mirror%20Reality'
    )

    def singbox(self, *outbounds):
        """A sing-box config shaped like the provider's SFI response."""
        return json.dumps({
            'dns': {}, 'log': {}, 'route': {}, 'inbounds': [], 'experimental': {},
            'outbounds': [
                {'type': 'selector', 'tag': 'select', 'outbounds': ['direct']},
                *outbounds,
                {'type': 'direct', 'tag': 'direct'},
            ],
        }).encode()

    def v2ray_array(self, *, security='none'):
        """The array of whole client configs returned to v2rayNG/Happ agents."""
        stream = {'network': 'tcp', 'security': security}
        if security == 'tls':
            stream['tlsSettings'] = {'serverName': 'sni.example', 'fingerprint': 'chrome'}
        return json.dumps([{
            'remarks': f'Mirror {index}',
            'routing': {'rules': []},
            'outbounds': [
                {
                    'protocol': 'vless',
                    'settings': {'vnext': [{
                        'address': f'mirror-{index}.example',
                        'port': 443,
                        'users': [{'id': f'synthetic-{index}', 'encryption': 'none'}],
                    }]},
                    'streamSettings': stream,
                },
                {'protocol': 'freedom', 'tag': 'direct'},
            ],
        } for index in range(3)]).encode()

    def test_singbox_document_serves_only_secure_endpoints_by_default(self):
        payload = self.singbox(self.plaintext_outbound, self.reality_outbound)

        self.assertEqual(views._sanitize_upstream_payload(payload), [self.reality_link])

    def test_singbox_reality_link_matches_our_own_field_order(self):
        payload = self.singbox(self.reality_outbound)

        link = views._sanitize_upstream_payload(payload)[0]

        self.assertEqual(link, self.reality_link)
        self.assertEqual(parse_qs(urlsplit(link).query)['serviceName'], ['synthetic-service'])

    def test_singbox_tls_without_reality_is_secure_and_omits_reality_fields(self):
        outbound = copy.deepcopy(self.reality_outbound)
        del outbound['tls']['reality']

        query = parse_qs(urlsplit(views._sanitize_upstream_payload(
            self.singbox(outbound))[0]).query)

        self.assertEqual(query['security'], ['tls'])
        self.assertNotIn('pbk', query)
        self.assertNotIn('sid', query)

    def test_singbox_reality_without_key_material_is_dropped(self):
        outbound = copy.deepcopy(self.reality_outbound)
        outbound['tls']['reality'] = {'enabled': True, 'public_key': ''}

        self.assertEqual(views._sanitize_upstream_payload(self.singbox(outbound)), [])

    def test_v2ray_array_is_parsed_and_gated_like_singbox(self):
        self.assertEqual(views._sanitize_upstream_payload(self.v2ray_array()), [])

        links = views._sanitize_upstream_payload(self.v2ray_array(security='tls'))

        self.assertEqual(links, [
            f'vless://synthetic-{index}@mirror-{index}.example:443?'
            f'type=tcp&security=tls&fp=chrome&sni=sni.example#Mirror%20{index}'
            for index in range(3)
        ])

    @override_settings(SUBSCRIPTION_BACKUP_ALLOW_PLAINTEXT_ENDPOINTS=True)
    def test_plaintext_endpoints_are_served_only_when_explicitly_enabled(self):
        payload = self.singbox(self.plaintext_outbound, self.reality_outbound)

        self.assertEqual(views._sanitize_upstream_payload(payload), [
            'vless://synthetic-plain-id@mirror-one.example:443?'
            'type=tcp&security=none#Mirror%20Plain',
            self.reality_link,
        ])

    @override_settings(SUBSCRIPTION_BACKUP_ALLOW_PLAINTEXT_ENDPOINTS=True)
    def test_hostile_and_malformed_documents_yield_no_links_and_no_exception(self):
        loopback = dict(self.plaintext_outbound, server='127.0.0.1')
        private = dict(self.plaintext_outbound, server='10.0.0.1')
        named_loopback = dict(self.plaintext_outbound, server='LocalHost')
        for payload in (
            b'{',
            b'{"outbounds": "not-a-list"}',
            b'[[[' + b'[' * 5000,
            b'[' * 5000 + b']' * 5000,
            json.dumps({'outbounds': [{'type': 'vless'}]}).encode(),
            self.singbox(loopback, private, named_loopback),
            self.singbox(dict(self.plaintext_outbound, server_port=True)),
            self.singbox(dict(self.plaintext_outbound, server_port=0)),
            self.singbox(dict(self.plaintext_outbound, uuid='id\r\nHost: evil')),
            self.singbox(dict(self.plaintext_outbound, server='host with space')),
            json.dumps([{'outbounds': 'not-a-list'}, 'not-a-config']).encode(),
        ):
            with self.subTest(payload=payload[:40]):
                self.assertEqual(views._sanitize_upstream_payload(payload), [])

    @override_settings(SUBSCRIPTION_BACKUP_ALLOW_PLAINTEXT_ENDPOINTS=True)
    def test_oversized_document_is_capped_at_the_endpoint_limit(self):
        outbounds = [dict(self.plaintext_outbound, server=f'mirror-{index}.example',
                          tag=f'Mirror {index}')
                     for index in range(views._MIRROR_MAX_ENDPOINTS * 4)]

        links = views._sanitize_upstream_payload(self.singbox(*outbounds))

        self.assertEqual(len(links), views._MIRROR_MAX_ENDPOINTS - 1)

    def test_unexpected_content_types_are_ignored(self):
        for payload in (
            b'proxies:\n  - name: mirror\n    type: vless\n    server: mirror.example\n',
            b'<!DOCTYPE html><html><body>subscription expired</body></html>',
            b'\x00\xff\xfe binary',
        ):
            with self.subTest(payload=payload[:20]):
                self.assertEqual(views._sanitize_upstream_payload(payload), [])

    @override_settings(SUBSCRIPTION_BACKUP_ALLOW_PLAINTEXT_ENDPOINTS=True)
    def test_provider_strings_cannot_inject_uri_structure(self):
        outbound = dict(self.plaintext_outbound,
                        uuid='id@evil.example/x?a=b#frag',
                        tag='Mirror#injected')

        link = views._sanitize_upstream_payload(self.singbox(outbound))[0]

        self.assertEqual(urlsplit(link).hostname, 'mirror-one.example')
        self.assertEqual(urlsplit(link).username, 'id%40evil.example%2Fx%3Fa%3Db%23frag')
        self.assertEqual(urlsplit(link).fragment, 'Mirror%23injected')

    def test_opaque_uri_list_sources_keep_their_byte_for_byte_contract(self):
        link = 'vless://synthetic@backup.example:443?type=tcp#Other'

        self.assertEqual(views._sanitize_upstream_payload((link + '\n').encode()), [link])
        self.assertEqual(
            views._sanitize_upstream_payload(base64.b64encode((link + '\n').encode())), [link])

    def test_user_agent_selects_the_format_and_rejects_header_injection(self):
        self.assertEqual(views._upstream_user_agent(), 'SPECIAL-subscription-backup/1')
        with self.settings(SUBSCRIPTION_BACKUP_UPSTREAM_USER_AGENT='SFI/1.9'):
            self.assertEqual(views._upstream_user_agent(), 'SFI/1.9')
        for value in ('', '   ', 'SFI/1.9\r\nX-Injected: 1', 'ага/1.0', 'a' * 129, ['SFI/1.9']):
            with self.subTest(value=value), self.settings(
                SUBSCRIPTION_BACKUP_UPSTREAM_USER_AGENT=value,
            ):
                self.assertEqual(views._upstream_user_agent(), 'SPECIAL-subscription-backup/1')

    @override_settings(SUBSCRIPTION_BACKUP_UPSTREAM_USER_AGENT='SFI/1.9')
    @patch('apps.subscriptions.views._resolve_public_upstream', return_value={'8.8.8.8'})
    @patch('apps.subscriptions.views.ssl.create_default_context')
    @patch('apps.subscriptions.views.socket.create_connection')
    def test_configured_agent_is_sent_to_the_provider(self, create_connection, create_context, resolve):
        tls_socket = _FakeTLSSocket(b'HTTP/1.1 200 OK\r\n\r\n{}')
        create_connection.return_value = Mock()
        create_context.return_value.wrap_socket.return_value = tls_socket

        views._fetch_upstream_payload('https://subscription.example/opaque')

        self.assertIn('User-Agent: SFI/1.9\r\n', b''.join(tls_socket.sent).decode('ascii'))

    @override_settings(
        SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=True,
        SUBSCRIPTION_BACKUP_UPSTREAM_URLS=['https://subscription.example/mirror'],
    )
    @patch('apps.subscriptions.views._fetch_upstream_payload')
    def test_plaintext_only_source_aggregates_to_no_links(self, fetch):
        views._clear_backup_cache()
        self.addCleanup(views._clear_backup_cache)
        fetch.return_value = self.singbox(self.plaintext_outbound)

        self.assertIsNone(views._backup_links())


class SubscriptionResponseCacheTests(SimpleTestCase):
    def test_success_and_not_found_responses_are_not_cacheable(self):
        for response in (views._no_cache_response(views.HttpResponse('ok')),
                         views._no_cache_response(views.HttpResponseNotFound())):
            self.assertEqual(response['Cache-Control'], 'private, no-store')
            self.assertEqual(response['Pragma'], 'no-cache')


class BackupSecretFileTests(SimpleTestCase):
    def _load(self, path):
        return self._load_secret(path)[0]

    def _load_secret(self, path):
        from bot import settings as bot_settings
        with patch.object(bot_settings.env, 'str', return_value=str(path)):
            return bot_settings._backup_secret_from_secret_file()

    def _secret_file(self, contents, mode=0o600):
        handle = tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8')
        self.addCleanup(lambda: os.path.exists(handle.name) and os.unlink(handle.name))
        handle.write(contents)
        handle.close()
        os.chmod(handle.name, mode)
        return Path(handle.name)

    def test_only_dict_with_list_of_strings_is_accepted(self):
        self.assertEqual(self._load(self._secret_file('{"upstream_urls": []}')), [])
        self.assertEqual(self._load(self._secret_file('null')), [])
        self.assertEqual(self._load(self._secret_file('"not-a-document"')), [])
        self.assertEqual(self._load(self._secret_file('{"upstream_urls": [1]}')), [])
        self.assertEqual(self._load(self._secret_file('{"upstream_urls": ["https://synthetic.example/sub"]}')),
                         ['https://synthetic.example/sub'])

    def test_absent_line_digest_list_preserves_existing_behavior(self):
        self.assertEqual(
            self._load_secret(self._secret_file(
                '{"upstream_urls": ["https://synthetic.example/sub"]}')),
            (['https://synthetic.example/sub'], None),
        )

    def test_valid_line_digest_list_is_exposed_without_values(self):
        digest = '0' * 64
        self.assertEqual(
            self._load_secret(self._secret_file(
                f'{{"upstream_urls": ["https://synthetic.example/sub"], "allowed_line_sha256": ["{digest}"]}}')),
            (['https://synthetic.example/sub'], [digest]),
        )

    def test_malformed_line_digest_list_fails_safe(self):
        self.assertEqual(
            self._load_secret(self._secret_file(
                '{"upstream_urls": ["https://synthetic.example/sub"], "allowed_line_sha256": ["A"]}')),
            ([], []),
        )

    def test_default_compose_device_fails_open(self):
        self.assertEqual(self._load('/dev/null'), [])

    def test_symlink_nonregular_and_wrong_mode_fail_open(self):
        target = self._secret_file('{"upstream_urls": []}')
        link = Path(f'{target}-link')
        os.symlink(target, link)
        self.addCleanup(lambda: os.path.lexists(link) and os.unlink(link))
        self.assertEqual(self._load(link), [])

        directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: directory.rmdir())
        os.chmod(directory, 0o600)
        self.assertEqual(self._load(directory), [])
        self.assertEqual(self._load(self._secret_file('{"upstream_urls": []}', 0o644)), [])


class SubscriptionLogRedactionTests(SimpleTestCase):
    def test_control_plane_warning_urls_are_redacted(self):
        from bot.logging_filters import _redact
        message = 'request failed https://panel.example.invalid/private/path?token=synthetic'
        self.assertEqual(_redact(message), 'request failed [REDACTED]')

    def test_configured_django_loggers_emit_only_redacted_output(self):
        from bot import settings as bot_settings
        configured = copy.deepcopy(bot_settings.LOGGING)
        stream = io.StringIO()
        configured['handlers']['console']['stream'] = stream
        logging.config.dictConfig(configured)

        logger = logging.getLogger('django.request')
        logger.warning('Not Found: /sub/synthetic-token-123')

        self.assertIn('/sub/[REDACTED]', stream.getvalue())
        self.assertNotIn('synthetic-token-123', stream.getvalue())
        for name in ('django', 'django.request', 'django.server'):
            configured_logger = logging.getLogger(name)
            self.assertFalse(configured_logger.propagate)
            self.assertEqual(len(configured_logger.handlers), 1)
            self.assertIsInstance(configured_logger.handlers[0], logging.StreamHandler)

    def test_ignore_patterns_match_all_required_secret_names(self):
        names = (
            'subscription-backup.json', 'my-subscription-backup-secret',
            'my_subscription_backup_secret', 'my-backup-subscription-secret',
            'my_backup_subscription_secret', '.environment.production',
        )
        required_patterns = {
            'subscription-backup.json', '*subscription-backup*', '*subscription_backup*',
            '*backup-subscription*', '*backup_subscription*', '.environment*',
        }
        for filename in ('.gitignore', '.dockerignore'):
            patterns = set(Path(filename).read_text().splitlines())
            self.assertTrue(required_patterns <= patterns)
            for name in names:
                self.assertTrue(any(fnmatchcase(name, pattern) for pattern in patterns), (filename, name))
