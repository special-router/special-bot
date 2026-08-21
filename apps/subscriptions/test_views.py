import base64
import copy
import datetime
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
from urllib.parse import parse_qs, quote, unquote, urlsplit

from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from apps.subscriptions import views
from apps.subscriptions.models import MirrorEndpointLiveness
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
        SUBSCRIPTION_BACKUP_TEST_USER_IDS=[],
        SUBSCRIPTION_BACKUP_ALL_USERS_ENABLED=True,
    )
    def test_full_rollout_reaches_an_id_no_allowlist_ever_named(self):
        # The point of the state: a customer created after the rollout is not
        # waiting on somebody to remember to extend a list.
        self.assertTrue(_is_backup_test_user(1))
        self.assertTrue(_is_backup_test_user(999999))

    @override_settings(
        SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=False,
        SUBSCRIPTION_BACKUP_ALL_USERS_ENABLED=True,
    )
    def test_full_rollout_still_obeys_the_master_gate(self):
        self.assertFalse(_is_backup_test_user(1))

    @override_settings(
        SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=True,
        SUBSCRIPTION_BACKUP_TEST_USER_IDS=[5],
        SUBSCRIPTION_BACKUP_ALL_USERS_ENABLED=False,
    )
    def test_full_rollout_off_leaves_the_allowlist_in_charge(self):
        self.assertTrue(_is_backup_test_user(5))
        self.assertFalse(_is_backup_test_user(6))

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


@override_settings(
    SUBSCRIPTION_BASE_URL='https://direct.example/sub',
    SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=False,
    SUBSCRIPTION_INTERNAL_INBOUNDS_ENABLED=False,
    SUBSCRIPTION_DIRECT_ADVERTISED_PORT=0,
    SUBSCRIPTION_STATUS_ENTRY_ENABLED=True,
    SUBSCRIPTION_XRAY_JSON_ENABLED=True,
    SUBSCRIPTION_XRAY_JSON_TEST_USER_IDS=[1],
)
@patch('apps.subscriptions.views._get_params', return_value={
    'public_key': 'synthetic-public-key', 'server_name': 'sni.example',
    'short_ids': ['synthetic-short-id'], 'port': 8443, 'network': 'tcp',
})
class XrayJsonSubscriptionTests(SimpleTestCase):
    """The raw Xray JSON branch served to Happ/v2rayNG User-Agents."""

    def _response(self, user_agent):
        subscription = SimpleNamespace(
            id=1, enabled=True,
            server=SimpleNamespace(id=1, inbound_id=5, client_vpn_host='relay.example:443',
                                   tariff=SimpleNamespace(price='7.00')),
            user_id=1, vpn_uuid='synthetic-local-id',
        )
        with patch('apps.subscriptions.views.UserVPN.objects') as user_vpn_objects, \
                patch('apps.subscriptions.views.TelegramUser.objects') as telegram_user_objects:
            user_vpn_objects.select_related.return_value.get.return_value = subscription
            telegram_user_objects.annotate_balance.return_value.filter.return_value.first.return_value = (
                SimpleNamespace(balance='70.00'))
            request = RequestFactory().get('/sub/synthetic', HTTP_USER_AGENT=user_agent)
            return views.subscription_proxy(request, 'synthetic')

    def test_happ_user_agent_gets_xray_json_with_balancer_and_ru_direct_rules(self, _params):
        response = self._response('Happ/2.0')

        self.assertEqual(response['Content-Type'], 'application/json')
        # The body is an array of named profiles, which is how a client renders
        # one entry per element; ours is first because it is the only endpoint
        # this deployment is accountable for.
        documents = json.loads(response.content)
        self.assertIsInstance(documents, list)
        document = documents[0]
        self.assertIn('remarks', document)
        self.assertEqual(document['routing']['balancers'][0]['tag'], 'own-l1')
        tags = {outbound['tag'] for outbound in document['outbounds']}
        self.assertEqual(tags, {'proxy-nl-direct', 'proxy-ru-relay', 'direct', 'block'})

        rule_index = {}
        for index, rule in enumerate(document['routing']['rules']):
            if rule.get('domain') == ['regexp:\\.ru$']:
                rule_index['ru_regexp'] = index
            if rule.get('ip') == ['geoip:ru']:
                rule_index['ru_geoip'] = index
            if rule.get('balancerTag') == 'own-l1' and rule.get('network'):
                rule_index['balancer_fallback'] = index
        self.assertLess(rule_index['ru_regexp'], rule_index['balancer_fallback'])
        self.assertLess(rule_index['ru_geoip'], rule_index['balancer_fallback'])

    def test_v2rayng_user_agent_gets_the_same_xray_json_format(self, _params):
        response = self._response('V2rayNG/1.9.9')

        self.assertEqual(response['Content-Type'], 'application/json')
        document = json.loads(response.content)[0]
        self.assertIn('burstObservatory', document)
        self.assertEqual(document['burstObservatory']['subjectSelector'], ['proxy'])

    def test_balancer_ping_config_targets_the_fastest_cycle_xray_allows(self, _params):
        # interval=10s is Xray-core's hard floor (anything smaller is silently
        # raised); sampling=1 means the very next probe decides Alive/dead,
        # instead of needing several cycles to evict stale-healthy samples.
        response = self._response('Happ/2.0')

        ping_config = json.loads(response.content)[0]['burstObservatory']['pingConfig']
        self.assertEqual(ping_config['interval'], '10s')
        self.assertEqual(ping_config['sampling'], 1)

    def test_unrecognized_user_agent_keeps_the_base64_contract(self, _params):
        response = self._response('curl/8.0')

        self.assertEqual(response['Content-Type'], 'text/plain')
        base64.b64decode(response.content)  # does not raise -- still the legacy body

    @override_settings(SUBSCRIPTION_XRAY_JSON_TEST_USER_IDS=[999])
    def test_a_customer_outside_the_rollout_keeps_the_working_format(self, _params):
        """Смена формата читается клиентом как «серверы пропали», а не как формат."""
        response = self._response('Happ/2.0')

        self.assertEqual(response['Content-Type'], 'text/plain')

    @override_settings(SUBSCRIPTION_XRAY_JSON_TEST_USER_IDS=[])
    def test_an_empty_rollout_list_means_nobody_not_everybody(self, _params):
        response = self._response('Happ/2.0')

        self.assertEqual(response['Content-Type'], 'text/plain')

    @override_settings(SUBSCRIPTION_XRAY_JSON_TEST_USER_IDS=[],
                       SUBSCRIPTION_XRAY_JSON_ALL_USERS_ENABLED=True)
    def test_opening_it_to_everyone_is_its_own_deliberate_switch(self, _params):
        response = self._response('Happ/2.0')

        self.assertEqual(response['Content-Type'], 'application/json')

    @override_settings(SUBSCRIPTION_XRAY_JSON_ENABLED=False)
    def test_flag_off_beats_a_matching_user_agent(self, _params):
        response = self._response('Happ/2.0')

        self.assertEqual(response['Content-Type'], 'text/plain')
        base64.b64decode(response.content)  # does not raise -- still the legacy body

    def test_headers_are_identical_between_json_and_base64_branches(self, _params):
        json_response = self._response('Happ/2.0')
        with override_settings(SUBSCRIPTION_XRAY_JSON_ENABLED=False):
            base64_response = self._response('Happ/2.0')

        for header in ('subscription-userinfo', 'profile-title', 'announce',
                       'support-url', 'profile-web-page-url', 'Profile-Update-Interval'):
            self.assertEqual(json_response.get(header), base64_response.get(header))


class XrayJsonDeviceGateTests(SimpleTestCase):
    """Device binding must gate the JSON branch exactly like the base64 one."""

    @override_settings(
        SUBSCRIPTION_BASE_URL='https://direct.example/sub',
        SUBSCRIPTION_XRAY_JSON_ENABLED=True,
        SUBSCRIPTION_XRAY_JSON_TEST_USER_IDS=[1],
    )
    @patch('apps.subscriptions.views._device_gate',
          return_value=(False, {'x-hwid-not-supported': 'true'}))
    @patch('apps.subscriptions.views._get_params')
    @patch('apps.subscriptions.views.UserVPN.objects')
    def test_a_refused_device_gets_404_even_with_a_matching_user_agent(
        self, user_vpn_objects, get_params, device_gate,
    ):
        user_vpn_objects.select_related.return_value.get.return_value = SimpleNamespace(
            id=1, enabled=True,
            server=SimpleNamespace(id=1, inbound_id=5, client_vpn_host='relay.example:443', tariff=None),
            user_id=1, vpn_uuid='synthetic-local-id',
        )

        response = views.subscription_proxy(
            RequestFactory().get('/sub/synthetic', HTTP_USER_AGENT='Happ/2.0'), 'synthetic')

        self.assertEqual(response.status_code, 404)
        get_params.assert_not_called()


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
        fetch.return_value = ({}, (self.opaque_link + '\n').encode())

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

        self.assertEqual(
            views._fetch_upstream_payload('https://subscription.example:8443/path?q=1'), ({}, b'1234'))

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
            return {}, payload

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
    # No tag in this class names a country, so every endpoint here lands in the
    # one unnamed group and is rendered with the generic label; what the policy
    # does with a country signal is asserted in MirrorLabelPolicyTests.
    generic = quote(views._MIRROR_UNKNOWN_REGION)
    reality_link = (
        'vless://synthetic-reality-id@mirror-two.example:8443?'
        'flow=xtls-rprx-vision&type=grpc&security=reality&pbk=synthetic-pbk&fp=chrome&'
        f'sni=sni.example&sid=ab01&spx=%2F&serviceName=synthetic-service#{generic}'
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

        outbound['tls']['reality'] = {'enabled': True, 'public_key': '', 'short_id': 'ab01'}

        self.assertEqual(views._sanitize_upstream_payload(self.singbox(outbound)), [])

    def test_singbox_reality_without_short_id_is_served_without_sid(self):
        """An empty ``shortIds`` list is a working Reality server, not a broken one."""
        outbound = copy.deepcopy(self.reality_outbound)
        del outbound['tls']['reality']['short_id']

        link = views._sanitize_upstream_payload(self.singbox(outbound))[0]

        self.assertEqual(link, self.reality_link.replace('sid=ab01&', ''))
        self.assertNotIn('sid', parse_qs(urlsplit(link).query))

    def test_singbox_reality_omits_every_absent_optional_field(self):
        outbound = {
            'type': 'vless',
            'tag': 'Mirror Bare',
            'server': 'mirror-three.example',
            'server_port': 8443,
            'uuid': 'synthetic-bare-id',
            'tls': {'enabled': True,
                    'reality': {'enabled': True, 'public_key': 'synthetic-pbk'}},
        }

        self.assertEqual(views._sanitize_upstream_payload(self.singbox(outbound)), [
            'vless://synthetic-bare-id@mirror-three.example:8443?'
            f'type=tcp&security=reality&pbk=synthetic-pbk&spx=%2F#{self.generic}',
        ])

    @override_settings(SUBSCRIPTION_BACKUP_MAX_ENTRIES_PER_REGION=3)
    def test_v2ray_array_is_parsed_and_gated_like_singbox(self):
        self.assertEqual(views._sanitize_upstream_payload(self.v2ray_array()), [])

        links = views._sanitize_upstream_payload(self.v2ray_array(security='tls'))

        self.assertEqual(links, [
            f'vless://synthetic-{index}@mirror-{index}.example:443?'
            f'type=tcp&security=tls&fp=chrome&sni=sni.example'
            f'#{self.generic}{quote(f" {index + 1}") if index else ""}'
            for index in range(3)
        ])

    @override_settings(SUBSCRIPTION_BACKUP_ALLOW_PLAINTEXT_ENDPOINTS=True,
                       SUBSCRIPTION_BACKUP_MAX_ENTRIES_PER_REGION=2)
    def test_plaintext_endpoints_are_served_only_when_explicitly_enabled(self):
        payload = self.singbox(self.plaintext_outbound, self.reality_outbound)

        self.assertEqual(views._sanitize_upstream_payload(payload), [
            'vless://synthetic-plain-id@mirror-one.example:443?'
            f'type=tcp&security=none#{self.generic}',
            self.reality_link.replace(f'#{self.generic}', f'#{self.generic}{quote(" 2")}'),
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

    @override_settings(SUBSCRIPTION_BACKUP_ALLOW_PLAINTEXT_ENDPOINTS=True,
                       SUBSCRIPTION_BACKUP_MAX_ENTRIES_PER_REGION=views._MIRROR_MAX_ENDPOINTS)
    def test_oversized_document_is_capped_at_the_endpoint_limit(self):
        outbounds = [dict(self.plaintext_outbound, server=f'mirror-{index}.example',
                          tag=f'Mirror {index}')
                     for index in range(views._MIRROR_MAX_ENDPOINTS * 8)]

        links = views._sanitize_upstream_payload(self.singbox(*outbounds))

        self.assertEqual(len(links), views._MIRROR_MAX_ENDPOINTS)

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
        # The tag never reaches the fragment at all now, injected or not.
        self.assertEqual(urlsplit(link).fragment, self.generic)

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
        fetch.return_value = ({}, self.singbox(self.plaintext_outbound))

        self.assertIsNone(views._backup_links())


class MirrorClientIdentityTests(SimpleTestCase):
    """A provider serves its real configuration only to a client it recognizes."""

    hwid = 'synthetic-installation-hwid-01'
    refused_headers = {'x-hwid-not-supported': 'true', 'x-hwid-limit': 'true'}
    accepted_headers = {'x-hwid-active': 'true'}
    regions = ('Fastest', 'Netherlands', 'Germany', 'Sweden', 'Norway',
               'USA', 'Kazakhstan', 'Japan', 'Russia')

    def setUp(self):
        super().setUp()
        views._clear_backup_cache()
        self.addCleanup(views._clear_backup_cache)

    def placeholder(self):
        """The instruction document served to an unidentified client.

        Its outbounds are plaintext and its tags are a message to the user, not
        endpoint names.
        """
        return json.dumps({'outbounds': [
            {'type': 'selector', 'tag': '→ Remnawave', 'outbounds': ['unsupported']},
            *({
                'type': 'vless',
                'tag': tag,
                'server': 'notice.example',
                'server_port': 443,
                'uuid': 'synthetic-placeholder-id',
                'tls': {'enabled': False},
            } for tag in ('Приложение не поддерживается', 'Поддерживаемые приложения:',
                          'Happ, V2RayTun, INCY, Koala Clash')),
        ]}).encode()

    def region_document(self, servers_per_region=9, tag=None):
        """A real multi-region answer: one group per region holding its servers."""
        tag = tag or (lambda region, index: f'srv-{index}')
        groups, servers = [], []
        for region_index, region in enumerate(self.regions):
            tags = [tag(region, f'{region_index}-{index}') for index in range(servers_per_region)]
            groups.append({'type': 'selector', 'tag': region, 'outbounds': tags})
            servers.extend({
                'type': 'vless',
                'tag': server_tag,
                'server': f'server-{region_index}-{index}.example',
                'server_port': 443,
                'uuid': f'synthetic-{region_index}-{index}',
                'tls': {
                    'enabled': True,
                    'server_name': 'sni.example',
                    'reality': {'enabled': True, 'public_key': 'synthetic-pbk', 'short_id': 'ab01'},
                },
            } for index, server_tag in enumerate(tags))
        return json.dumps({'outbounds': [
            {'type': 'selector', 'tag': '→ Remnawave', 'outbounds': list(self.regions)},
            *groups, *servers, {'type': 'direct', 'tag': 'direct'},
        ]}).encode()

    def remarks(self, links):
        return [unquote(urlsplit(link).fragment) for link in links]

    def test_placeholder_document_is_refused_rather_than_counted_as_empty(self):
        payload = self.placeholder()

        with self.assertRaises(views._UpstreamPlaceholderDocument):
            views._sanitize_upstream_payload(payload, self.refused_headers)

    @override_settings(SUBSCRIPTION_BACKUP_ALLOW_PLAINTEXT_ENDPOINTS=True)
    def test_placeholder_is_never_served_even_where_plaintext_is_allowed(self):
        with self.assertRaises(views._UpstreamPlaceholderDocument):
            views._sanitize_upstream_payload(self.placeholder(), self.refused_headers)

    def test_a_refused_identity_alone_does_not_condemn_a_real_configuration(self):
        links = views._sanitize_upstream_payload(self.region_document(servers_per_region=1),
                                                 self.refused_headers)

        self.assertEqual(len(links), len(self.regions))

    def test_a_plaintext_source_that_accepts_us_stays_an_ordinary_empty_source(self):
        payload = self.placeholder()

        self.assertEqual(views._sanitize_upstream_payload(payload, self.accepted_headers), [])
        self.assertEqual(views._sanitize_upstream_payload(payload), [])

    @override_settings(
        SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=True,
        SUBSCRIPTION_BACKUP_UPSTREAM_URLS=['https://subscription.example/mirror'],
    )
    @patch('apps.subscriptions.views._fetch_upstream_payload')
    def test_placeholder_and_empty_source_are_recorded_differently(self, fetch):
        url = 'https://subscription.example/mirror'
        fetch.return_value = (self.refused_headers, self.placeholder())

        with self.assertLogs('apps.subscriptions.views', 'WARNING') as logged:
            self.assertEqual(views._cached_upstream_links(url), [])

        self.assertIn('placeholder', logged.output[0])
        self.assertNotIn(url, logged.output[0])
        self.assertNotIn('mirror', logged.output[0])
        fetch.return_value = (self.accepted_headers, self.placeholder())
        with self.assertNoLogs('apps.subscriptions.views', 'WARNING'):
            self.assertEqual(views._cached_upstream_links(url), [])

    def test_multi_region_document_keeps_one_entry_per_region(self):
        links = views._sanitize_upstream_payload(self.region_document(), self.accepted_headers)

        remarks = self.remarks(links)
        self.assertEqual(len(links), len(self.regions))
        # The Dutch entry is numbered against our own line, which every response
        # carries above the mirrored ones.
        self.assertEqual(remarks, [
            '🇩🇪 Германия', '🇰🇿 Казахстан', '🇳🇱 Нидерланды 2', '🇳🇴 Норвегия', '🇷🇺 Россия',
            '🇺🇸 США', '🇸🇪 Швеция', '🇯🇵 Япония', '🌐 Резерв',
        ])

    @override_settings(SUBSCRIPTION_BACKUP_MAX_ENTRIES_PER_REGION=views._MIRROR_MAX_ENDPOINTS)
    def test_the_endpoint_cap_still_holds_at_the_real_document_size(self):
        links = views._sanitize_upstream_payload(self.region_document(servers_per_region=40))

        self.assertEqual(len(links), views._MIRROR_MAX_ENDPOINTS)

    def test_identity_headers_travel_only_with_a_usable_identifier(self):
        self.assertEqual(views._upstream_client_headers(), [])
        with self.settings(SUBSCRIPTION_BACKUP_UPSTREAM_HWID=self.hwid,
                           SUBSCRIPTION_BACKUP_UPSTREAM_DEVICE_OS='Android',
                           SUBSCRIPTION_BACKUP_UPSTREAM_OS_VERSION='14',
                           SUBSCRIPTION_BACKUP_UPSTREAM_DEVICE_MODEL='Pixel 8'):
            self.assertEqual(views._upstream_client_headers(), [
                ('x-hwid', self.hwid),
                ('x-device-os', 'Android'),
                ('x-ver-os', '14'),
                ('x-device-model', 'Pixel 8'),
            ])
        for value in ('', 'short', 'a' * 65, 'has space', 'inject\r\nX: 1', ['valid-hwid-1234']):
            with self.subTest(hwid=value), self.settings(
                SUBSCRIPTION_BACKUP_UPSTREAM_HWID=value,
                SUBSCRIPTION_BACKUP_UPSTREAM_DEVICE_OS='Android',
            ):
                self.assertEqual(views._upstream_client_headers(), [])
        with self.settings(SUBSCRIPTION_BACKUP_UPSTREAM_HWID=self.hwid,
                           SUBSCRIPTION_BACKUP_UPSTREAM_DEVICE_OS='Android\r\nX: 1',
                           SUBSCRIPTION_BACKUP_UPSTREAM_DEVICE_MODEL='м' * 4):
            self.assertEqual(views._upstream_client_headers(), [('x-hwid', self.hwid)])

    @override_settings(
        SUBSCRIPTION_BACKUP_UPSTREAM_USER_AGENT='Happ/1.0',
        SUBSCRIPTION_BACKUP_UPSTREAM_HWID=hwid,
        SUBSCRIPTION_BACKUP_UPSTREAM_DEVICE_OS='Android',
        SUBSCRIPTION_BACKUP_UPSTREAM_OS_VERSION='14',
        SUBSCRIPTION_BACKUP_UPSTREAM_DEVICE_MODEL='Pixel 8',
    )
    @patch('apps.subscriptions.views._resolve_public_upstream', return_value={'8.8.8.8'})
    @patch('apps.subscriptions.views.ssl.create_default_context')
    @patch('apps.subscriptions.views.socket.create_connection')
    def test_configured_identity_is_sent_to_the_provider(
        self, create_connection, create_context, resolve,
    ):
        tls_socket = _FakeTLSSocket(b'HTTP/1.1 200 OK\r\nx-hwid-active: true\r\n\r\n{}')
        create_connection.return_value = Mock()
        create_context.return_value.wrap_socket.return_value = tls_socket

        headers, _payload = views._fetch_upstream_payload('https://subscription.example/opaque')

        request = b''.join(tls_socket.sent).decode('ascii')
        self.assertIn('User-Agent: Happ/1.0\r\n', request)
        self.assertIn(f'x-hwid: {self.hwid}\r\n', request)
        self.assertIn('x-device-os: Android\r\n', request)
        self.assertIn('x-ver-os: 14\r\n', request)
        self.assertIn('x-device-model: Pixel 8\r\n', request)
        self.assertEqual(headers.get('x-hwid-active'), 'true')

    @override_settings(SUBSCRIPTION_BACKUP_UPSTREAM_HWID='')
    @patch('apps.subscriptions.views._resolve_public_upstream', return_value={'8.8.8.8'})
    @patch('apps.subscriptions.views.ssl.create_default_context')
    @patch('apps.subscriptions.views.socket.create_connection')
    def test_an_unset_identity_sends_no_device_headers(
        self, create_connection, create_context, resolve,
    ):
        tls_socket = _FakeTLSSocket(b'HTTP/1.1 200 OK\r\n\r\n{}')
        create_connection.return_value = Mock()
        create_context.return_value.wrap_socket.return_value = tls_socket

        views._fetch_upstream_payload('https://subscription.example/opaque')

        self.assertNotIn('x-hwid', b''.join(tls_socket.sent).decode('ascii'))


class MirrorLabelPolicyTests(SimpleTestCase):
    """What a paying customer reads on an endpoint we did not build.

    The fixture is shaped like the live answer: a root selector named after the
    provider, one flagged group per region, and servers whose tags are the
    provider's own inventory numbering. None of those three strings is
    something a customer of this deployment may be shown.
    """

    provider = '→ Remnawave'
    regions = (('🇪🇺', 'Fastest'), ('🇳🇱', 'Netherlands'), ('🇩🇪', 'Germany'),
               ('🇸🇪', 'Sweden'), ('🇳🇴', 'Norway'), ('🇺🇸', 'USA'),
               ('🇰🇿', 'Kazakhstan'), ('🇯🇵', 'Japan'), ('🇷🇺', 'Russia'))
    expected = ['🇩🇪 Германия', '🇪🇺 Европа', '🇰🇿 Казахстан', '🇳🇱 Нидерланды 2', '🇳🇴 Норвегия',
                '🇷🇺 Россия', '🇺🇸 США', '🇸🇪 Швеция', '🇯🇵 Япония']

    def setUp(self):
        super().setUp()
        views._clear_backup_cache()
        self.addCleanup(views._clear_backup_cache)

    def document(self, servers_per_region=9, flags=True, reverse=False):
        """A provider document in the live shape, optionally reordered."""
        groups, servers, server_tags = [], [], []
        for region_index, (flag, region) in enumerate(self.regions):
            tags = [f'{flag} {region.upper()}_VLESS_{index + 1}' if flags
                    else f'{region.upper()}_VLESS_{index + 1}'
                    for index in range(servers_per_region)]
            server_tags.extend(tags)
            groups.append({'type': 'selector',
                           'tag': f'{flag} {region}' if flags else region,
                           'outbounds': tags})
            servers.extend({
                'type': 'vless',
                'tag': server_tag,
                'server': f'server-{region_index}-{index}.example',
                'server_port': 443,
                'uuid': f'synthetic-{region_index}-{index}',
                'tls': {
                    'enabled': True,
                    'server_name': 'sni.example',
                    'reality': {'enabled': True, 'public_key': 'synthetic-pbk', 'short_id': 'ab01'},
                },
            } for index, server_tag in enumerate(tags))
        # The root selector names the servers too, which is how the provider's
        # own product name became every endpoint's region before this policy.
        outbounds = [
            {'type': 'selector', 'tag': self.provider,
             'outbounds': [group['tag'] for group in groups] + server_tags},
            *groups, *servers, {'type': 'direct', 'tag': 'direct'},
        ]
        if reverse:
            outbounds.reverse()
        return json.dumps({'outbounds': outbounds}).encode()

    def remarks(self, links):
        return [unquote(urlsplit(link).fragment) for link in links]

    def test_the_provider_product_name_never_reaches_a_customer(self):
        links = views._sanitize_upstream_payload(self.document())

        rendered = ' '.join(self.remarks(links))
        self.assertNotIn('Remnawave', rendered)
        self.assertNotIn('VLESS', rendered)
        self.assertEqual(self.remarks(links), self.expected)

    def test_a_raw_inventory_name_is_replaced_by_the_place_it_sits_in(self):
        remarks = self.remarks(views._sanitize_upstream_payload(self.document(flags=False)))

        self.assertNotIn('SWEDEN_VLESS_1', ' '.join(remarks))
        self.assertIn('🇸🇪 Швеция', remarks)
        # 'Fastest' names no place, so it keeps an honest generic label rather
        # than borrowing the provider's word for it.
        self.assertIn(views._MIRROR_UNKNOWN_REGION, remarks)

    def test_nine_regions_collapse_to_one_entry_each_and_stay_put(self):
        links = views._sanitize_upstream_payload(self.document())

        self.assertEqual(len(links), 9)
        self.assertEqual(self.remarks(links), self.expected)
        # Same servers, reordered document: identical list, same chosen hosts.
        self.assertEqual(views._sanitize_upstream_payload(self.document(reverse=True)), links)
        self.assertEqual([urlsplit(link).hostname for link in links],
                         [f'server-{index}-0.example' for index in (2, 0, 6, 1, 4, 8, 5, 3, 7)])

    @override_settings(SUBSCRIPTION_BACKUP_MAX_ENTRIES_PER_REGION=3)
    def test_raising_the_per_region_cap_yields_more_entries(self):
        remarks = self.remarks(views._sanitize_upstream_payload(self.document()))

        self.assertEqual(len(remarks), 27)
        self.assertEqual(remarks[:3], ['🇩🇪 Германия', '🇩🇪 Германия 2', '🇩🇪 Германия 3'])

    @override_settings(
        SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=True,
        SUBSCRIPTION_BACKUP_UPSTREAM_URLS=['https://subscription.example/mirror'],
        SUBSCRIPTION_BACKUP_MAX_ENTRIES_PER_REGION=40,
        SUBSCRIPTION_BACKUP_MAX_MIRROR_ENTRIES=5,
    )
    @patch('apps.subscriptions.views._fetch_upstream_payload')
    def test_the_overall_cap_bounds_what_a_provider_can_add(self, fetch):
        fetch.return_value = ({}, self.document(servers_per_region=40))

        self.assertEqual(len(views._backup_links()), 5)

    @override_settings(
        SUBSCRIPTION_BASE_URL='https://direct.example/sub',
        SUBSCRIPTION_DIRECT_ADVERTISED_PORT=0,
        SUBSCRIPTION_STATUS_ENTRY_ENABLED=True,
        SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=True,
        SUBSCRIPTION_BACKUP_TEST_USER_IDS=[1],
        SUBSCRIPTION_BACKUP_UPSTREAM_URLS=['https://subscription.example/mirror'],
    )
    @patch('apps.subscriptions.views._fetch_upstream_payload')
    @patch('apps.subscriptions.views._get_params', return_value={
        'public_key': 'synthetic-public-key', 'server_name': 'sni.example',
        'short_ids': ['synthetic-short-id'], 'port': 8443, 'network': 'tcp',
    })
    @patch('apps.subscriptions.views.TelegramUser.objects')
    @patch('apps.subscriptions.views.UserVPN.objects')
    def test_our_own_three_lines_survive_a_full_provider_document(
        self, user_vpn_objects, telegram_user_objects, _params, fetch,
    ):
        fetch.return_value = ({}, self.document())
        user_vpn_objects.select_related.return_value.get.return_value = SimpleNamespace(
            id=1, enabled=True,
            server=SimpleNamespace(id=1, inbound_id=5, client_vpn_host='relay.example:443', tariff=None),
            user_id=1, vpn_uuid='synthetic-local-id',
        )
        telegram_user_objects.annotate_balance.return_value.filter.return_value.first.return_value = None

        lines = base64.b64decode(views.subscription_proxy(
            RequestFactory().get('/sub/synthetic'), 'synthetic').content).decode().splitlines()

        self.assertEqual(len(lines), 3 + 9)
        self.assertEqual(self.remarks(lines[:3]),
                         ['📊 Подписка-подписка окончена', '🇳🇱 Нидерланды',
                          '🇳🇱 Нидерланды белые списки'])
        self.assertEqual(urlsplit(lines[1]).hostname, 'direct.example')
        self.assertEqual(urlsplit(lines[2]).hostname, 'relay.example')
        self.assertEqual(self.remarks(lines[3:]), self.expected)

    def test_a_hostile_label_still_cannot_inject_uri_structure(self):
        """A label is evidence about a country, never characters to render.

        The two labels that resolve keep their country and lose everything the
        provider wrote around it; the ones this module refuses to read fall back
        to the generic label, because the group naming them is the provider's
        own root selector, which names no place.
        """
        backup = views._MIRROR_UNKNOWN_REGION
        for label, expected in (
            ('🇯🇵 Japan#x?a=b&c=d/../', '🇯🇵 Япония'),
            ('../../🇯🇵', '🇯🇵 Япония'),
            ('🇯🇵 Japan\r\nX-Injected: 1', backup),
            ('🇯🇵 Japan' * 100, backup),
            ('🇯🇵\x00Japan', backup),
        ):
            with self.subTest(label=label):
                document = json.loads(self.document(servers_per_region=1))
                for outbound in document['outbounds']:
                    if outbound.get('server') == 'server-3-0.example':
                        outbound['tag'] = label

                link = [candidate for candidate in views._sanitize_upstream_payload(
                    json.dumps(document).encode())
                    if urlsplit(candidate).hostname == 'server-3-0.example'][0]

                parsed = urlsplit(link)
                self.assertEqual(parsed.port, 443)
                self.assertNotIn('\n', link)
                self.assertNotIn('#', parsed.fragment)
                self.assertEqual(unquote(parsed.fragment), expected)


@override_settings(
    SUBSCRIPTION_BASE_URL='https://direct.example/sub',
    SUBSCRIPTION_DIRECT_ADVERTISED_PORT=0,
    SUBSCRIPTION_STATUS_ENTRY_ENABLED=True,
    SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=False,
)
class EndpointLabelTests(SimpleTestCase):
    """What a line tells a customer: where it exits, and whether it survives a whitelist.

    Two lines of the same country are not substitutes — the suffixed one is
    reachable from a whitelisted network that refuses the direct host — so the
    property is part of the label, and it is composed from the endpoint's own
    flag rather than written onto the one line that happens to need it today.
    """

    params = {'public_key': 'synthetic-public-key', 'server_name': 'sni.example',
              'short_ids': ['synthetic-short-id'], 'port': 8443, 'network': 'tcp'}

    def remarks(self, links):
        return [unquote(urlsplit(link).fragment) for link in links]

    def subscription(self, client_vpn_host='relay.example:443', document=None):
        """Render one customer's document, with mirrored sources only on request."""
        views._clear_backup_cache()
        self.addCleanup(views._clear_backup_cache)
        with patch('apps.subscriptions.views._fetch_upstream_payload',
                   return_value=({}, document or b'')), \
                patch('apps.subscriptions.views._get_params', return_value=self.params), \
                patch('apps.subscriptions.views.TelegramUser.objects') as telegram_user_objects, \
                patch('apps.subscriptions.views.UserVPN.objects') as user_vpn_objects:
            user_vpn_objects.select_related.return_value.get.return_value = SimpleNamespace(
                id=801, enabled=True, user_id=801, vpn_uuid='synthetic-local-id',
                server=SimpleNamespace(id=1, inbound_id=5, tariff=None,
                                       client_vpn_host=client_vpn_host),
            )
            telegram_user_objects.annotate_balance.return_value.filter.return_value.first.return_value = None
            response = views.subscription_proxy(
                RequestFactory().get('/sub/synthetic'), 'synthetic')
        return base64.b64decode(response.content).decode().splitlines()

    def mirrored(self, **overrides):
        raw = {'host': 'se-1.example', 'port': 443, 'uuid': 'synthetic-mirror-id',
               'security': 'reality', 'remark': '🇸🇪 SWEDEN_VLESS_1',
               'public_key': 'synthetic-pbk'}
        raw.update(overrides)
        return views._normalized_mirror_endpoint(raw)

    def provider_document(self, *regions):
        """A provider answer holding one flagged server per named region."""
        return json.dumps({'outbounds': [{
            'type': 'vless',
            'tag': f'{flag} {name.upper()}_VLESS_1',
            'server': f'{name.lower()}-1.example',
            'server_port': 443,
            'uuid': f'synthetic-{name.lower()}-id',
            'tls': {'enabled': True, 'server_name': 'sni.example',
                    'reality': {'enabled': True, 'public_key': 'synthetic-pbk',
                                'short_id': 'ab01'}},
        } for flag, name in regions]}).encode()

    def test_the_direct_line_names_its_country_and_claims_no_route(self):
        self.assertEqual(self.remarks(self.subscription())[1], '🇳🇱 Нидерланды')

    def test_the_relay_line_names_the_same_country_and_its_whitelist_property(self):
        """Both endpoints we operate sit at the top, ours before any fallback."""
        lines = self.subscription()

        self.assertEqual(self.remarks(lines)[2], '🇳🇱 Нидерланды белые списки')
        self.assertEqual(urlsplit(lines[2]).hostname, 'relay.example')

    def test_a_server_with_no_relay_renders_no_suffixed_line(self):
        self.assertEqual(self.remarks(self.subscription(client_vpn_host='')),
                         ['📊 Подписка-подписка окончена', '🇳🇱 Нидерланды'])

    @override_settings(
        SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=True,
        SUBSCRIPTION_BACKUP_TEST_USER_IDS=[801],
        SUBSCRIPTION_BACKUP_UPSTREAM_URLS=['https://subscription.example/mirror'],
    )
    def test_our_dutch_line_stays_bare_and_a_mirrored_one_is_numbered(self):
        """Two identical entries would leave a customer nothing to choose on.

        The number disambiguates and says nothing about whose endpoint it is.
        Ours is the unsuffixed one because the view renders both endpoints we
        operate above every mirrored line; this pins the whole order, so a later
        change cannot quietly demote our own endpoint to the numbered position
        or push it back below a provider's.
        """
        remarks = self.remarks(
            self.subscription(document=self.provider_document(('🇳🇱', 'Netherlands'),
                                                              ('🇸🇪', 'Sweden'))))

        self.assertEqual(remarks, ['📊 Подписка-подписка окончена', '🇳🇱 Нидерланды',
                                   '🇳🇱 Нидерланды белые списки', '🇳🇱 Нидерланды 2',
                                   '🇸🇪 Швеция'])
        self.assertEqual(remarks.count('🇳🇱 Нидерланды'), 1)
        self.assertLess(remarks.index('🇳🇱 Нидерланды'), remarks.index('🇳🇱 Нидерланды 2'))
        # The seed counts both of our labels, so moving the suffixed line above
        # the mirrors cannot renumber one of them onto a mirrored endpoint.
        self.assertEqual(remarks.count('🇳🇱 Нидерланды белые списки'), 1)
        self.assertEqual(remarks.count('🇳🇱 Нидерланды 2'), 1)
        for remark in remarks[1:]:
            self.assertNotIn('Резерв', remark)
            self.assertNotIn('Backup', remark)

    def test_a_mirrored_endpoint_claims_no_whitelist_and_says_only_its_country(self):
        endpoint = self.mirrored()

        self.assertFalse(endpoint['whitelisted'])
        self.assertEqual(self.remarks([views._build_mirror_vless(endpoint)]), ['🇸🇪 Швеция'])

    @override_settings(SUBSCRIPTION_BACKUP_WHITELIST_SNI_SUFFIXES=['x5.ru'])
    def test_the_suffix_follows_the_property_and_not_the_position_or_the_host(self):
        """A whitelist-capable endpoint is labelled by construction, wherever it sits."""
        whitelisted = self.mirrored(server_name='id.x5.ru')

        self.assertEqual(whitelisted['remark'], '🇸🇪 Швеция белые списки')
        self.assertEqual(whitelisted['host'], self.mirrored()['host'])
        self.assertEqual(views._endpoint_label('SE'), '🇸🇪 Швеция')
        self.assertEqual(views._endpoint_label('SE', whitelisted=True), '🇸🇪 Швеция белые списки')
        # A property we can name on a region we cannot still names the property.
        self.assertEqual(views._endpoint_label('', whitelisted=True),
                         f'{views._MIRROR_UNKNOWN_REGION} белые списки')

    def test_the_suffixed_remark_survives_the_fragment_encoding(self):
        line = self.subscription()[-1]
        fragment = urlsplit(line).fragment

        self.assertEqual(unquote(fragment), '🇳🇱 Нидерланды белые списки')
        # A literal space or Cyrillic byte in a fragment ends the URI early in a
        # client that splits a subscription on whitespace.
        self.assertTrue(line.isascii())
        self.assertEqual(fragment.count('%20'), 3)
        for character in ' ?&=/':
            self.assertNotIn(character, fragment)

    def test_every_rendered_region_name_is_written_in_russian(self):
        for code, name in views._MIRROR_REGION_NAMES.items():
            with self.subTest(code=code):
                self.assertFalse(any(character.isascii() and character.isalpha()
                                     for character in name))

    def test_a_place_a_provider_writes_in_english_still_resolves_to_one(self):
        """The parse side stays Latin; only what we render is translated."""
        for label, expected in (('SWEDEN_VLESS_1', '🇸🇪 Швеция'),
                                ('United States', '🇺🇸 США'),
                                ('Hong Kong', '🇭🇰 Гонконг'),
                                ('Holland', '🇳🇱 Нидерланды')):
            with self.subTest(label=label):
                self.assertEqual(views._endpoint_label(views._mirror_region_code(label)), expected)

    def test_regions_order_by_their_russian_name_and_leave_the_unnamed_last(self):
        codes = ('JP', 'DE', 'US', '', 'SE', 'GB', 'FI')

        self.assertEqual(
            [views._endpoint_label(code) for code in sorted(codes, key=views._region_order_key)],
            ['🇬🇧 Великобритания', '🇩🇪 Германия', '🇺🇸 США', '🇫🇮 Финляндия', '🇸🇪 Швеция',
             '🇯🇵 Япония', '🌐 Резерв'])


class MirrorEndpointChoiceTests(SimpleTestCase):
    """Which endpoint a region resolves to, on a document shaped like the live one.

    The provider offers 31 outbounds over 16 distinct hosts. One of them is
    Cloudflare's resolver, carried under nine different flags and labelled by
    the provider as its fastest node; one real host is shared by eight regions;
    the rest belong to a single region each. A customer reported the whole
    subscription as dead when every region resolved to the resolver, which
    accepts a connection and completes no handshake.
    """

    # (flag, region, [(host, port)]) — the region's members in the provider's
    # own order, resolver entries included.
    regions = (
        ('🇪🇺', 'Fastest', (('1.1.1.1', 443), ('shared.example', 443))),
        ('🇳🇱', 'Netherlands', (('1.1.1.1', 443), ('shared.example', 443),
                                ('nl-1.example', 443), ('nl-2.example', 443))),
        ('🇩🇪', 'Germany', (('1.1.1.1', 443), ('shared.example', 443),
                            ('de-1.example', 8443), ('de-2.example', 443))),
        ('🇸🇪', 'Sweden', (('1.1.1.1', 443), ('shared.example', 443),
                           ('se-1.example', 59406), ('se-2.example', 443))),
        ('🇳🇴', 'Norway', (('1.1.1.1', 443), ('shared.example', 443), ('no-1.example', 443))),
        ('🇺🇸', 'USA', (('1.1.1.1', 443), ('shared.example', 443), ('us-1.example', 8443))),
        ('🇰🇿', 'Kazakhstan', (('1.1.1.1', 443), ('shared.example', 443), ('kz-1.example', 443))),
        ('🇯🇵', 'Japan', (('1.1.1.1', 443), ('shared.example', 443), ('jp-1.example', 443))),
        ('🇷🇺', 'Russia', (('1.1.1.1', 443), ('9.9.9.9', 443),
                           ('ru-1.example', 443), ('ru-2.example', 443))),
        # Every candidate this region offers is a resolver, so it must not be
        # rendered at all rather than rendered as something that never connects.
        ('🇦🇲', 'Armenia', (('8.8.8.8', 443),)),
    )
    expected_hosts = ['de-1.example', 'shared.example', 'kz-1.example', 'nl-1.example',
                      'no-1.example', 'ru-1.example', 'us-1.example', 'se-1.example',
                      'jp-1.example']
    expected_remarks = ['🇩🇪 Германия', '🇪🇺 Европа', '🇰🇿 Казахстан', '🇳🇱 Нидерланды 2',
                        '🇳🇴 Норвегия', '🇷🇺 Россия', '🇺🇸 США', '🇸🇪 Швеция',
                        '🇯🇵 Япония']

    def document(self, reverse=False):
        """The provider's answer: a root selector, region groups, then servers."""
        groups, servers, server_tags = [], [], []
        for flag, region, members in self.regions:
            tags = [f'{flag} {region.upper()}_VLESS_{index + 1}'
                    for index in range(len(members))]
            server_tags.extend(tags)
            groups.append({'type': 'selector', 'tag': f'{flag} {region}', 'outbounds': tags})
            servers.extend({
                'type': 'vless',
                'tag': server_tag,
                'server': host,
                'server_port': port,
                'uuid': f'synthetic-{server_tag}',
                'tls': {
                    'enabled': True,
                    'server_name': 'sni.example',
                    'reality': {'enabled': True, 'public_key': 'synthetic-pbk', 'short_id': 'ab01'},
                },
            } for server_tag, (host, port) in zip(tags, members))
        outbounds = [
            {'type': 'selector', 'tag': '→ Remnawave',
             'outbounds': [group['tag'] for group in groups] + server_tags},
            *groups, *servers, {'type': 'direct', 'tag': 'direct'},
        ]
        if reverse:
            outbounds.reverse()
        return json.dumps({'outbounds': outbounds}).encode()

    def hosts(self, links):
        return [urlsplit(link).hostname for link in links]

    def remarks(self, links):
        return [unquote(urlsplit(link).fragment) for link in links]

    def test_the_fixture_matches_the_live_documents_shape(self):
        outbounds = json.loads(self.document())['outbounds']
        servers = [outbound for outbound in outbounds if outbound['type'] == 'vless']

        self.assertEqual(len(servers), 31)
        self.assertEqual(len({server['server'] for server in servers}), 16)
        self.assertEqual(sum(server['server'] == '1.1.1.1' for server in servers), 9)
        self.assertEqual(sum(server['server'] == 'shared.example' for server in servers), 8)
        self.assertEqual({server['server_port'] for server in servers}, {443, 8443, 59406})

    def test_a_public_resolver_is_never_rendered_under_any_flag(self):
        links = views._sanitize_upstream_payload(self.document())

        self.assertNotIn('1.1.1.1', self.hosts(links))
        self.assertFalse(set(self.hosts(links)) & {'1.1.1.1', '8.8.8.8', '9.9.9.9'})

    def test_every_excluded_address_is_refused_as_an_endpoint_host(self):
        for address in views._MIRROR_EXCLUDED_HOSTS:
            with self.subTest(address=str(address)):
                self.assertFalse(views._safe_endpoint_host(str(address)))
        # The ranges ``_is_public_unicast`` already refused stay refused, and an
        # ordinary public address is still a usable endpoint.
        self.assertFalse(views._safe_endpoint_host('127.0.0.1'))
        self.assertFalse(views._safe_endpoint_host('169.254.10.10'))
        self.assertFalse(views._safe_endpoint_host('203.0.113.10'))
        self.assertTrue(views._safe_endpoint_host('13.13.13.13'))

    def test_each_region_keeps_its_own_server_where_the_document_offers_one(self):
        links = views._sanitize_upstream_payload(self.document())

        self.assertEqual(self.hosts(links), self.expected_hosts)
        self.assertEqual(len(set(self.hosts(links))), len(links))

    def test_a_host_shared_by_eight_regions_loses_to_a_single_region_host(self):
        """Only the region with no server of its own falls back to the shared host."""
        links = views._sanitize_upstream_payload(self.document())

        self.assertEqual(self.hosts(links).count('shared.example'), 1)
        self.assertEqual(self.remarks(links)[self.hosts(links).index('shared.example')],
                         '🇪🇺 Европа')

    def test_a_region_whose_only_candidate_is_excluded_disappears(self):
        remarks = self.remarks(views._sanitize_upstream_payload(self.document()))

        self.assertNotIn('🇦🇲 Армения', remarks)
        self.assertEqual(remarks, self.expected_remarks)

    def test_selection_is_identical_across_two_runs_and_a_reordered_document(self):
        links = views._sanitize_upstream_payload(self.document())

        self.assertEqual(views._sanitize_upstream_payload(self.document()), links)
        self.assertEqual(views._sanitize_upstream_payload(self.document(reverse=True)), links)

    def test_a_non_default_port_survives_selection(self):
        links = views._sanitize_upstream_payload(self.document())
        ports = dict(zip(self.hosts(links), (urlsplit(link).port for link in links)))

        self.assertEqual(ports['de-1.example'], 8443)
        self.assertEqual(ports['se-1.example'], 59406)
        self.assertEqual(ports['us-1.example'], 8443)

    def test_the_providers_inventory_names_never_reach_a_remark(self):
        rendered = ' '.join(self.remarks(views._sanitize_upstream_payload(self.document())))

        for leaked in ('VLESS', 'Remnawave', 'SWEDEN_VLESS_1', 'RUSSIA_VLESS_3', 'Fastest'):
            self.assertNotIn(leaked, rendered)

    def test_a_bridge_inventory_tag_renders_as_a_place_or_as_nothing_named(self):
        """The tags the provider writes for its own transit nodes, exactly as sent."""
        for tag, expected in (('L2_BRIDGE_VLESS_1', views._MIRROR_UNKNOWN_REGION),
                              ('L3_BRIDGE_VLESS_2', views._MIRROR_UNKNOWN_REGION),
                              ('BRIDGE_RUSSIA_VLESS_1', '🇷🇺 Россия'),
                              ('SWEDEN_VLESS_1', '🇸🇪 Швеция')):
            with self.subTest(tag=tag):
                payload = json.dumps({'outbounds': [{
                    'type': 'vless', 'tag': tag, 'server': 'bridge.example',
                    'server_port': 8443, 'uuid': 'synthetic-bridge-id',
                    'tls': {'enabled': True, 'reality': {
                        'enabled': True, 'public_key': 'synthetic-pbk'}},
                }]}).encode()

                remark = self.remarks(views._sanitize_upstream_payload(payload))[0]

                self.assertEqual(remark, expected)
                self.assertNotIn('VLESS', remark)
                self.assertNotIn('BRIDGE', remark.upper())


class _MirrorDocumentMixin:
    """One provider answer per named region, in the shape the live one has."""

    def document(self, regions):
        outbounds = []
        for flag, name, members in regions:
            for index, member in enumerate(members):
                host, port, server_name = member
                outbounds.append({
                    'type': 'vless',
                    'tag': f'{flag} {name.upper()}_VLESS_{index + 1}',
                    'server': host,
                    'server_port': port,
                    'uuid': f'synthetic-{host}',
                    'tls': {'enabled': True, 'server_name': server_name,
                            'reality': {'enabled': True, 'public_key': 'synthetic-pbk',
                                        'short_id': 'ab01'}},
                })
        return json.dumps({'outbounds': outbounds}).encode()

    def hosts(self, links):
        return [urlsplit(link).hostname for link in links]

    def remarks(self, links):
        return [unquote(urlsplit(link).fragment) for link in links]


class MirrorEndpointLivenessTests(_MirrorDocumentMixin, TestCase):
    """Which candidate a region resolves to once something has actually dialled.

    Eleven of the live provider's thirty-one outbounds do not complete a
    handshake, and the Russian region alone offers six candidates with two of
    them dead. Selection never dials — a request has an eight-second fetch
    budget — so it reads verdicts a probe wrote out of band, and every case
    where it has none must land exactly where the blind pick landed.
    """

    regions = (
        ('🇷🇺', 'Russia', (('ru-1.example', 443, 'sni.example'),
                           ('ru-2.example', 443, 'sni.example'),
                           ('ru-3.example', 443, 'sni.example'))),
        ('🇸🇪', 'Sweden', (('se-1.example', 443, 'sni.example'),)),
    )

    def payload(self):
        return self.document(self.regions)

    def verdict(self, host, *, alive, port=443, age_seconds=0):
        MirrorEndpointLiveness.objects.create(
            host=host, port=port, alive=alive,
            checked_at=timezone.now() - datetime.timedelta(seconds=age_seconds))

    def rendered(self):
        return views._sanitize_upstream_payload(self.payload())

    def blind(self):
        """What the deployment rendered before any verdict existed."""
        with override_settings(SUBSCRIPTION_BACKUP_LIVENESS_ENABLED=False):
            return self.rendered()

    def test_the_blind_selection_takes_the_first_candidate_of_each_region(self):
        self.assertEqual(self.hosts(self.blind()), ['ru-1.example', 'se-1.example'])

    @override_settings(SUBSCRIPTION_BACKUP_LIVENESS_ENABLED=True)
    def test_a_dead_verdict_removes_a_candidate_and_the_next_one_is_chosen(self):
        self.verdict('ru-1.example', alive=False)

        self.assertEqual(self.hosts(self.rendered()), ['ru-2.example', 'se-1.example'])

    @override_settings(SUBSCRIPTION_BACKUP_LIVENESS_ENABLED=True)
    def test_a_region_whose_every_candidate_is_dead_disappears(self):
        """A region with nothing left is dropped, never rendered as a dead line."""
        for host in ('ru-1.example', 'ru-2.example', 'ru-3.example'):
            self.verdict(host, alive=False)

        links = self.rendered()

        self.assertEqual(self.hosts(links), ['se-1.example'])
        self.assertNotIn('🇷🇺 Россия', self.remarks(links))

    @override_settings(SUBSCRIPTION_BACKUP_LIVENESS_ENABLED=True)
    def test_no_verdicts_at_all_reproduce_todays_output_exactly(self):
        self.assertEqual(MirrorEndpointLiveness.objects.count(), 0)

        self.assertEqual(self.rendered(), self.blind())

    @override_settings(SUBSCRIPTION_BACKUP_LIVENESS_ENABLED=True,
                       SUBSCRIPTION_BACKUP_LIVENESS_MAX_AGE_SECONDS=3600)
    def test_an_expired_verdict_is_ignored(self):
        """A server down an hour ago may be up now, so an old verdict stops counting."""
        self.verdict('ru-1.example', alive=False, age_seconds=7200)

        self.assertEqual(self.rendered(), self.blind())

    @override_settings(SUBSCRIPTION_BACKUP_LIVENESS_ENABLED=True)
    def test_a_candidate_known_to_answer_outranks_one_nobody_dialled(self):
        self.verdict('ru-3.example', alive=True)

        self.assertEqual(self.hosts(self.rendered()), ['ru-3.example', 'se-1.example'])

    @override_settings(SUBSCRIPTION_BACKUP_LIVENESS_ENABLED=True)
    def test_a_verdict_belongs_to_one_address_and_port(self):
        self.verdict('ru-1.example', alive=False, port=8443)

        self.assertEqual(self.rendered(), self.blind())

    @override_settings(SUBSCRIPTION_BACKUP_LIVENESS_ENABLED=False)
    def test_the_flag_off_reads_no_verdict_and_issues_no_query(self):
        self.verdict('ru-1.example', alive=False)

        with self.assertNumQueries(0):
            links = self.rendered()

        self.assertEqual(self.hosts(links), ['ru-1.example', 'se-1.example'])

    @override_settings(SUBSCRIPTION_BACKUP_LIVENESS_ENABLED=True)
    def test_a_database_that_cannot_answer_costs_the_improvement_not_the_list(self):
        """One unavailable table must not become a 500 on every subscription refresh."""
        with patch('apps.subscriptions.views.MirrorEndpointLiveness.objects.filter',
                   side_effect=RuntimeError('database is locked')):
            links = self.rendered()

        self.assertEqual(links, self.blind())


class MirrorWhitelistSniTests(_MirrorDocumentMixin, SimpleTestCase):
    """Rendering the endpoints that keep working when a region is cut to a whitelist.

    The provider advertises the bypass and implements none of it in routing:
    three rules, no domain logic. It is in ``tls.server_name`` — two live nodes
    announce a large Russian retailer, whose domain stays reachable so payments
    and shops keep working, and a Reality handshake wearing that name passes
    inspection that blocks everything else. Both nodes exit in Russia, so the
    line stays Russian; only the suffix is new.
    """

    regions = (
        ('🇷🇺', 'Russia', (('ru-1.example', 443, 'camouflage.example'),
                           ('l1-bridge.example', 443, 'id.x5.example'),
                           ('l2-bridge.example', 59406, 'id.x5.example'))),
        ('🇸🇪', 'Sweden', (('se-1.example', 443, 'camouflage.example'),)),
    )

    def payload(self):
        return self.document(self.regions)

    def rendered(self):
        return views._sanitize_upstream_payload(self.payload())

    def test_a_declared_suffix_renders_its_own_line_carrying_the_suffix(self):
        with override_settings(SUBSCRIPTION_BACKUP_WHITELIST_SNI_SUFFIXES=['x5.example']):
            links = self.rendered()

        self.assertEqual(self.remarks(links),
                         ['🇷🇺 Россия', '🇷🇺 Россия белые списки', '🇷🇺 Россия белые списки 2',
                          '🇸🇪 Швеция'])
        self.assertEqual(self.hosts(links),
                         ['ru-1.example', 'l1-bridge.example', 'l2-bridge.example',
                          'se-1.example'])

    def test_the_whitelist_line_does_not_consume_the_regions_ordinary_slot(self):
        """One per region still means one ordinary Russian server, plus the bypass."""
        with override_settings(SUBSCRIPTION_BACKUP_WHITELIST_SNI_SUFFIXES=['x5.example'],
                               SUBSCRIPTION_BACKUP_MAX_ENTRIES_PER_REGION=1):
            remarks = self.remarks(self.rendered())

        self.assertEqual(remarks.count('🇷🇺 Россия'), 1)
        self.assertEqual(sum('белые списки' in remark for remark in remarks), 2)
        self.assertLess(remarks.index('🇷🇺 Россия'), remarks.index('🇷🇺 Россия белые списки'))

    def test_an_empty_suffix_list_changes_nothing(self):
        """Undeclared, the two bypass nodes are three ordinary Russian candidates.

        So the region collapses to one line again and the pick is the host
        ordering this shipped with — the default renders exactly what it did
        before whitelist camouflage was a concept here.
        """
        with override_settings(SUBSCRIPTION_BACKUP_WHITELIST_SNI_SUFFIXES=[]):
            links = self.rendered()

        self.assertEqual(self.remarks(links), ['🇷🇺 Россия', '🇸🇪 Швеция'])
        self.assertEqual(self.hosts(links), ['l1-bridge.example', 'se-1.example'])

    def test_a_tag_naming_a_bridge_is_never_the_signal(self):
        """``BRIDGE_`` outbounds are byte-identical twins; only the SNI is evidence."""
        with override_settings(SUBSCRIPTION_BACKUP_WHITELIST_SNI_SUFFIXES=['nothing.example']):
            remarks = self.remarks(self.rendered())

        self.assertNotIn('🇷🇺 Россия белые списки', remarks)
        for remark in remarks:
            self.assertNotIn('BRIDGE', remark.upper())

    def test_a_neighbouring_registration_does_not_match_a_declared_suffix(self):
        for server_name, expected in (('id.x5.example', True), ('x5.example', True),
                                      ('ID.X5.EXAMPLE.', True), ('notx5.example', False),
                                      ('x5.example.net', False), ('', False)):
            with self.subTest(server_name=server_name), \
                    override_settings(SUBSCRIPTION_BACKUP_WHITELIST_SNI_SUFFIXES=['x5.example']):
                self.assertIs(views._whitelist_capable(server_name), expected)

    def test_a_malformed_suffix_list_declares_nothing(self):
        for suffixes in (None, 'x5.example', [None, 5], ['   '], ['.']):
            with self.subTest(suffixes=suffixes), \
                    override_settings(SUBSCRIPTION_BACKUP_WHITELIST_SNI_SUFFIXES=suffixes):
                self.assertFalse(views._whitelist_capable('id.x5.example'))

    def test_the_country_stays_the_exit_country_and_never_reads_as_foreign(self):
        """L1 exits at its own Russian address; a foreign flag here would be a lie."""
        with override_settings(SUBSCRIPTION_BACKUP_WHITELIST_SNI_SUFFIXES=['x5.example']):
            remarks = self.remarks(self.rendered())

        for remark in remarks:
            if 'белые списки' in remark:
                self.assertTrue(remark.startswith('🇷🇺 Россия'))

    @override_settings(
        SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=True,
        SUBSCRIPTION_BACKUP_UPSTREAM_URLS=['https://subscription.example/mirror'],
        SUBSCRIPTION_BACKUP_MAX_MIRROR_ENTRIES=2,
        SUBSCRIPTION_BACKUP_WHITELIST_SNI_SUFFIXES=['x5.example'],
    )
    def test_the_global_mirror_cap_still_binds_on_whitelist_lines(self):
        """Our own status, Direct and Relay lines stay findable whatever renders here."""
        views._clear_backup_cache()
        self.addCleanup(views._clear_backup_cache)

        with patch('apps.subscriptions.views._fetch_upstream_payload',
                   return_value=({}, self.payload())):
            links = views._backup_links()

        self.assertEqual(self.remarks(links), ['🇷🇺 Россия', '🇷🇺 Россия белые списки'])


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


@override_settings(
    SUBSCRIPTION_BASE_URL='https://direct.example/sub',
    SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=False,
    SUBSCRIPTION_INTERNAL_INBOUNDS_ENABLED=False,
    SUBSCRIPTION_XRAY_JSON_ENABLED=True,
    SUBSCRIPTION_XRAY_JSON_TEST_USER_IDS=[],
)
@patch('apps.subscriptions.views._get_params', return_value={
    'public_key': 'synthetic-public-key', 'server_name': 'sni.example',
    'short_ids': ['synthetic-short-id'], 'port': 8443, 'network': 'tcp',
})
class XrayJsonPerClientRolloutTests(SimpleTestCase):
    """Формат раскатывается по клиенту: проверен один — едет один."""

    def _response(self, user_agent):
        subscription = SimpleNamespace(
            id=1, enabled=True,
            server=SimpleNamespace(id=1, inbound_id=5, client_vpn_host='relay.example:443',
                                   tariff=SimpleNamespace(price='7.00')),
            user_id=1, vpn_uuid='synthetic-local-id',
        )
        with patch('apps.subscriptions.views.UserVPN.objects') as user_vpn_objects, \
                patch('apps.subscriptions.views.TelegramUser.objects') as telegram_user_objects:
            user_vpn_objects.select_related.return_value.get.return_value = subscription
            telegram_user_objects.annotate_balance.return_value.filter.return_value.first.return_value = (
                SimpleNamespace(balance='70.00'))
            request = RequestFactory().get('/sub/synthetic', HTTP_USER_AGENT=user_agent)
            return views.subscription_proxy(request, 'synthetic')

    @override_settings(SUBSCRIPTION_XRAY_JSON_ROLLED_OUT_CLIENTS=['happ'])
    def test_the_checked_client_gets_it_without_being_on_a_list(self, _params):
        self.assertEqual(self._response('Happ/2.0')['Content-Type'], 'application/json')

    @override_settings(SUBSCRIPTION_XRAY_JSON_ROLLED_OUT_CLIENTS=['happ'])
    def test_the_unchecked_client_keeps_the_working_format(self, _params):
        """v2rayNG читает тот же JSON своим кодом; его отказ выглядел бы так же."""
        self.assertEqual(self._response('V2rayNG/1.9.9')['Content-Type'], 'text/plain')

    @override_settings(SUBSCRIPTION_XRAY_JSON_ROLLED_OUT_CLIENTS=['happ', 'v2rayng'])
    def test_both_clients_once_both_are_checked(self, _params):
        self.assertEqual(self._response('V2rayNG/1.9.9')['Content-Type'], 'application/json')

    @override_settings(SUBSCRIPTION_XRAY_JSON_ROLLED_OUT_CLIENTS=[])
    def test_an_empty_rollout_falls_back_to_the_per_person_list(self, _params):
        self.assertEqual(self._response('Happ/2.0')['Content-Type'], 'text/plain')

    @override_settings(SUBSCRIPTION_XRAY_JSON_ROLLED_OUT_CLIENTS=['happ'])
    def test_an_unknown_client_is_still_untouched(self, _params):
        self.assertEqual(self._response('curl/8.0')['Content-Type'], 'text/plain')


@override_settings(
    SUBSCRIPTION_BASE_URL='https://direct.example/sub',
    SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=False,
    SUBSCRIPTION_INTERNAL_INBOUNDS_ENABLED=False,
    SUBSCRIPTION_XRAY_JSON_ENABLED=True,
    SUBSCRIPTION_XRAY_JSON_ROLLED_OUT_CLIENTS=['happ'],
)
@patch('apps.subscriptions.views._get_params', return_value={
    'public_key': 'synthetic-public-key', 'server_name': 'sni.example',
    'short_ids': ['synthetic-short-id'], 'port': 8443, 'network': 'tcp',
})
class XrayJsonClientRoutingTests(SimpleTestCase):
    def _response(self, user_agent):
        subscription = SimpleNamespace(
            id=1, enabled=True,
            server=SimpleNamespace(id=1, inbound_id=5, client_vpn_host='relay.example:443',
                                   tariff=SimpleNamespace(price='7.00')),
            user_id=1, vpn_uuid='synthetic-local-id',
        )
        with patch('apps.subscriptions.views.UserVPN.objects') as user_vpn_objects, \
                patch('apps.subscriptions.views.TelegramUser.objects') as telegram_user_objects:
            user_vpn_objects.select_related.return_value.get.return_value = subscription
            telegram_user_objects.annotate_balance.return_value.filter.return_value.first.return_value = (
                SimpleNamespace(balance='70.00'))
            request = RequestFactory().get('/sub/synthetic', HTTP_USER_AGENT=user_agent)
            return views.subscription_proxy(request, 'synthetic')

    def test_the_document_disables_the_client_s_own_routing(self, _params):
        """Два набора правил разом дают профиль, который соединяется и никуда не ведёт."""
        self.assertEqual(self._response('Happ/2.0')['routing-enable'], '0')

    def test_the_link_list_leaves_client_routing_alone(self, _params):
        """У списка ссылок своих правил нет, и глушить клиентские незачем."""
        self.assertNotIn('routing-enable', self._response('curl/8.0'))
