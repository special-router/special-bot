"""Per-user subscription proxy.

Serves ``/sub/<sub_id>`` as a base64 payload of VLESS links built from the
Django ``UserVPN`` record, so each user receives *their own* client UUID rather
than the first client of the 3x-ui inbound (which is what the built-in 3x-ui
``/sub/`` endpoint returns).

Reality parameters (publicKey, shortId, serverNames) are fetched from the 3x-ui
API and cached in-process for a few minutes to avoid hammering the panel on
every subscription refresh.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import ipaddress
import json
import logging
import socket
import ssl
import subprocess
import threading
import time
from functools import lru_cache
from urllib.parse import quote, unquote_to_bytes, urlencode, urlsplit

from django.http import HttpResponse, HttpResponseNotFound
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from apps.servers.models import Server
from apps.subscriptions.devices import (
    client_hwid, client_metadata, hwid_strict, register_device, valid_hwid)
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN
from utils.py3xui.async_api import AsyncApi


logger = logging.getLogger(__name__)

# In-process cache of inbound Reality params: (fetched_at, params).
_PARAM_TTL_SECONDS = 300


@lru_cache(maxsize=4)
def _reality_params(server_id: int, inbound_id: int) -> tuple[float, dict]:
    """Return (fetched_at, params). Callers must refresh when older than TTL."""
    loop = asyncio.new_event_loop()
    try:
        return (time.time(), loop.run_until_complete(_fetch_params(server_id, inbound_id)))
    finally:
        loop.close()


async def _fetch_params(server_id: int, inbound_id: int) -> dict:
    server = await Server.objects.aget(id=server_id)
    api = AsyncApi(server.vpn_url, server.vpn_username, server.vpn_password)
    await api.login()
    inbound = await api.inbound.get_by_id(inbound_id)
    rr = inbound.stream_settings.reality_settings
    return {
        'public_key': rr.get('settings').get('publicKey'),
        'server_name': rr.get('serverNames')[0],
        'short_ids': rr.get('shortIds'),
        'port': inbound.port,
        'network': inbound.stream_settings.network,
        'inbound_id': inbound_id,
    }


def _get_params(server_id: int, inbound_id: int) -> dict:
    fetched_at, params = _reality_params(server_id, inbound_id)
    if time.time() - fetched_at > _PARAM_TTL_SECONDS:
        _reality_params.cache_clear()
        fetched_at, params = _reality_params(server_id, inbound_id)
    return params


# The rollout intentionally recognizes only the production-validated targets.
# Values in configuration are public routing metadata, never client or Reality
# credentials.  Target membership is checked live for every subscription read.
_INTERNAL_EXPECTED = {
    7: (39329, 'tcp', 'reality'),
    9: (46517, 'tcp', 'reality'),
    13: (27914, 'tcp', 'reality'),
    10: (8080, 'grpc', 'reality'),
}
_INTERNAL_PROFILE_TTL_SECONDS = 300
_INTERNAL_PROFILE_CACHE: dict[tuple[int, int], tuple[float, str, dict]] = {}
_INTERNAL_PROFILE_CACHE_LOCK = threading.RLock()


def _config_internal_endpoints() -> list[dict]:
    """Validate the narrow, public-only canary configuration or return no targets."""
    from django.conf import settings
    endpoints = getattr(settings, 'SUBSCRIPTION_INTERNAL_ENDPOINTS', [])
    if not isinstance(endpoints, list) or not endpoints:
        return []
    configured, ids, ports = [], set(), set()
    for endpoint in endpoints:
        if not isinstance(endpoint, dict) or set(endpoint) != {'inbound_id', 'advertised_port', 'label'}:
            return []
        inbound_id, advertised_port, label = (
            endpoint.get('inbound_id'), endpoint.get('advertised_port'), endpoint.get('label'))
        if (type(inbound_id) is not int or type(advertised_port) is not int
                or not isinstance(label, str) or not label.strip() or len(label) > 128
                or any(character in label for character in '\r\n\x00')
                or inbound_id not in _INTERNAL_EXPECTED
                or inbound_id in ids or advertised_port in ports):
            return []
        expected_port, _network, _security = _INTERNAL_EXPECTED[inbound_id]
        # Inbound 10's x-ui backend is :8080, but it is reached via its public
        # nginx gRPC frontend on :80 and must never be advertised as :8080.
        if advertised_port != (80 if inbound_id == 10 else expected_port):
            return []
        ids.add(inbound_id)
        ports.add(advertised_port)
        configured.append({
            'inbound_id': inbound_id,
            'advertised_port': advertised_port,
            'label': label,
        })
    return configured


def _is_internal_test_user(user_vpn_id: int) -> bool:
    """Fail open unless the fixed UserVPN 801 rollout is exactly configured."""
    from django.conf import settings
    if not getattr(settings, 'SUBSCRIPTION_INTERNAL_INBOUNDS_ENABLED', False):
        return False
    # This feature is a one-user canary, not a generic audience mechanism.
    # Reject malformed, reordered, duplicate, or broadened allowlists.
    if getattr(settings, 'SUBSCRIPTION_INTERNAL_TEST_USER_IDS', []) != [801]:
        return False
    return user_vpn_id == 801 and bool(_config_internal_endpoints())


def _mapping(value) -> dict:
    if isinstance(value, dict):
        return value
    if hasattr(value, 'model_dump'):
        return value.model_dump(by_alias=True)
    if hasattr(value, 'dict'):
        return value.dict()
    return {}


def _value(value, snake_name: str, default=None):
    data = _mapping(value)
    camel_name = snake_name.split('_')[0] + ''.join(part.title() for part in snake_name.split('_')[1:])
    if snake_name in data:
        return data[snake_name]
    if camel_name in data:
        return data[camel_name]
    return getattr(value, snake_name, getattr(value, camel_name, default))


def _first_nonempty(value) -> str:
    if isinstance(value, list):
        value = next((item for item in value if isinstance(item, str) and item), '')
    return value if isinstance(value, str) else ''


def _normalized_internal_snapshot(inbound, requested_uuid: str) -> dict | None:
    """Normalize only fields that gate internal link rendering; never log them."""
    stream = _value(inbound, 'stream_settings', {})
    reality = _value(stream, 'reality_settings', {})
    reality_settings = _value(reality, 'settings', {})
    clients = _value(_value(inbound, 'settings', {}), 'clients', [])
    if not isinstance(clients, list):
        return None
    membership = []
    for client in clients:
        if str(_value(client, 'id', '')) == requested_uuid:
            try:
                expiry = int(_value(client, 'expiry_time', 0) or 0)
            except (TypeError, ValueError):
                return None
            membership.append((bool(_value(client, 'enable', False)), expiry))
    grpc = _value(stream, 'grpc_settings', {})
    return {
        'enabled': bool(_value(inbound, 'enable', False)),
        'port': _value(inbound, 'port'),
        'protocol': str(_value(inbound, 'protocol', '')).lower(),
        'network': str(_value(stream, 'network', '')).lower(),
        'security': str(_value(stream, 'security', '')).lower(),
        'public_key': _value(reality_settings, 'public_key', ''),
        'server_name': _first_nonempty(_value(reality, 'server_names', [])),
        'short_id': _first_nonempty(_value(reality, 'short_ids', [])),
        'service_name': _value(grpc, 'service_name', ''),
        'membership': membership,
    }


async def _read_internal_snapshots(server_id: int, inbound_ids: list[int], requested_uuid: str) -> dict[int, dict] | None:
    """Read all canary targets twice through one authenticated API session."""
    server = await Server.objects.aget(id=server_id)
    api = AsyncApi(server.vpn_url, server.vpn_username, server.vpn_password)
    await api.login()
    previous = None
    # Two bounded full rounds make one target's transient short response fail
    # the whole internal batch without repeating authentication per endpoint.
    for _round in range(2):
        current: dict[int, dict] = {}
        for inbound_id in inbound_ids:
            snapshot = _normalized_internal_snapshot(
                await api.inbound.get_raw_config_by_id(inbound_id), requested_uuid)
            if snapshot is None:
                return None
            current[inbound_id] = snapshot
        if previous is not None and current == previous:
            return current
        previous = current
    return None


def _stable_internal_snapshots(server_id: int, inbound_ids: list[int], requested_uuid: str) -> dict[int, dict] | None:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(asyncio.wait_for(
            _read_internal_snapshots(server_id, inbound_ids, requested_uuid), timeout=5))
    except Exception:
        return None
    finally:
        loop.close()


def _stable_internal_snapshot(server_id: int, inbound_id: int, requested_uuid: str) -> dict | None:
    """Compatibility helper; batch rendering uses _stable_internal_snapshots."""
    result = _stable_internal_snapshots(server_id, [inbound_id], requested_uuid)
    return result.get(inbound_id) if result else None


def _internal_profile(server_id: int, inbound_id: int, snapshot: dict) -> dict | None:
    """Return a cached static profile only after the current live gate succeeded."""
    static = {
        key: snapshot[key] for key in (
            'enabled', 'port', 'protocol', 'network', 'security', 'public_key',
            'server_name', 'short_id', 'service_name')
    }
    try:
        fingerprint = json.dumps(static, sort_keys=True, separators=(',', ':'))
    except (TypeError, ValueError):
        return None
    key = (server_id, inbound_id)
    now = time.monotonic()
    with _INTERNAL_PROFILE_CACHE_LOCK:
        cached = _INTERNAL_PROFILE_CACHE.get(key)
        if cached and cached[0] > now and cached[1] == fingerprint:
            return cached[2]
    expected_port, expected_network, expected_security = _INTERNAL_EXPECTED[inbound_id]
    if (not snapshot['enabled'] or snapshot['port'] != expected_port
            or snapshot['protocol'] != 'vless' or snapshot['network'] != expected_network
            or snapshot['security'] != expected_security):
        return None
    if not all(isinstance(snapshot[field], str) and snapshot[field]
               for field in ('public_key', 'server_name', 'short_id')):
        return None
    if inbound_id == 10 and (not isinstance(snapshot['service_name'], str)
                             or not snapshot['service_name']):
        return None
    profile = {
        'public_key': snapshot['public_key'],
        'server_name': snapshot['server_name'],
        'short_ids': [snapshot['short_id']],
        'network': expected_network,
        'service_name': snapshot['service_name'] if inbound_id == 10 else '',
    }
    with _INTERNAL_PROFILE_CACHE_LOCK:
        if len(_INTERNAL_PROFILE_CACHE) >= 32:
            _INTERNAL_PROFILE_CACHE.pop(next(iter(_INTERNAL_PROFILE_CACHE)))
        _INTERNAL_PROFILE_CACHE[key] = (now + _INTERNAL_PROFILE_TTL_SECONDS, fingerprint, profile)
    return profile


def _internal_links(server_id: int, requested_uuid: str) -> list[str]:
    """Render a batch-gated TCP/gRPC canary; uncertainty emits no internal lines."""
    from django.conf import settings
    try:
        sub_domain = urlsplit(settings.SUBSCRIPTION_BASE_URL).hostname
    except (TypeError, ValueError):
        return []
    if not sub_domain:
        return []
    endpoints = _config_internal_endpoints()
    snapshots = _stable_internal_snapshots(
        server_id, [endpoint['inbound_id'] for endpoint in endpoints], requested_uuid)
    if snapshots is None:
        return []
    links = []
    for endpoint in endpoints:
        inbound_id = endpoint['inbound_id']
        snapshot = snapshots.get(inbound_id)
        if snapshot is None:
            return []
        memberships = snapshot['membership']
        now_ms = int(time.time() * 1000)
        if len(memberships) != 1 or not memberships[0][0] or (memberships[0][1] and memberships[0][1] <= now_ms):
            return []
        profile = _internal_profile(server_id, inbound_id, snapshot)
        if profile is None:
            return []
        links.append(_build_vless(
            requested_uuid, sub_domain, endpoint['advertised_port'], endpoint['label'], profile,
            flow='', service_name=profile['service_name']))
    return links


def _build_vless(uuid: str, host: str, port: int, remark: str, params: dict, flow: str = '',
                  fingerprint: str = 'chrome', service_name: str = '') -> str:
    """Build a VLESS URI with encoded query values and explicit transport fields.

    The ordered TCP fields intentionally serialize identically to the deployed
    Direct/Relay output for normal Reality values.  New transports add only
    their protocol-defined fields rather than leaking TCP assumptions.
    """
    network = params.get('network', 'tcp')
    query_fields = [
        ('type', network),
        ('security', 'reality'),
        ('pbk', params['public_key']),
        ('fp', fingerprint),
        ('sni', params['server_name']),
        ('sid', params['short_ids'][0]),
        ('spx', '/'),
    ]
    if flow:
        query_fields.insert(0, ('flow', flow))
    if network == 'grpc' and service_name:
        query_fields.append(('serviceName', service_name))
    query = urlencode(query_fields, quote_via=quote)
    return f"vless://{uuid}@{host}:{port}?{query}#{quote(remark)}"


@csrf_exempt
@require_GET
def subscription_proxy(request, sub_id: str):
    try:
        user_vpn = UserVPN.objects.select_related('server', 'user').get(sub_id=sub_id)
    except UserVPN.DoesNotExist:
        return _refused(request)

    if not user_vpn.enabled:
        return _refused(request)

    served, hwid_headers = _device_gate(request, user_vpn)
    if not served:
        return _refused(request)

    server = user_vpn.server
    params = _get_params(server.id, server.inbound_id)

    # Balance / remaining days for the status remark and the expiry header.
    # Negative balances exist (a manual debit can outrun the balance), and they
    # must not become a term: no entitlement is zero days, never minus two.
    user = TelegramUser.objects.annotate_balance().filter(id=user_vpn.user_id).first()
    price = float(server.tariff.price) if server.tariff else 0.0
    balance = float(getattr(user, 'balance', 0) or 0) if user else 0.0
    days = max(int(balance // price), 0) if price > 0 else 0
    status_label = f'осталось {days} дней' if days > 0 else 'подписка окончена'

    # Client endpoint hosts.
    # Direct = public NL sub domain on the inbound port.
    # Relay  = the client_vpn_host stored on the server (e.g. the RU relay front).
    relay_host, relay_port = _endpoint(server.client_vpn_host, params['port'])
    sub_domain = settings_relays().SUBSCRIPTION_BASE_URL.split('/')[2].split(':')[0]  # hostname only
    direct_host = sub_domain
    # Advertise the shared public listener when configured; xray may then bind
    # its inbound privately without changing what any client dials.
    direct_port = getattr(settings_relays(), 'SUBSCRIPTION_DIRECT_ADVERTISED_PORT', 0) or params['port']

    uuid_str = str(user_vpn.vpn_uuid)
    # Preserve the deployed legacy client contract. Most existing control-plane
    # clients have no Vision flow, and forcing it in the generated subscription
    # makes those links intermittently land on an incompatible same-port
    # listener. Vision may be promoted only by an explicit per-client migration.
    flow = ''

    links = []
    # 1) Status entry (non-working) first, matching the happ UX.  It exists only
    # to show the remaining term to a client that reads no headers; the same
    # number now also ships in ``subscription-userinfo``, so this is retirable
    # once a real client is seen rendering that header.
    if getattr(settings_relays(), 'SUBSCRIPTION_STATUS_ENTRY_ENABLED', True):
        links.append(_build_vless(uuid_str, '127.0.0.1', 1, f'📊 Подписка-{status_label}', params, flow=''))
    # 2) Direct NL primary.
    links.append(_build_vless(uuid_str, direct_host, direct_port, '🇳🇱 NL Direct', params, flow=flow))
    # 3) Same-origin internal transport canary. Every candidate independently
    # stable-reads its own live inbound and silently omits on any uncertainty.
    if _is_internal_test_user(user_vpn.id):
        links.extend(_internal_links(server.id, uuid_str))
    # 4) External backup endpoints (feature-gated test group).
    backup_links = _backup_links() if _is_backup_test_user(user_vpn.id) else None
    if backup_links:
        links.extend(backup_links)
    # 5) RU relay (only if configured).
    if relay_host:
        links.append(_build_vless(uuid_str, relay_host, relay_port, '🇳🇱 NL Relay', params, flow=flow))

    payload = '\n'.join(links) + '\n'
    encoded = base64.b64encode(payload.encode('utf-8'))
    resp = HttpResponse(encoded, content_type='text/plain')
    resp['Profile-Update-Interval'] = '12'
    _with_headers(resp, _client_ui_headers(days))
    return _no_cache_response(_with_headers(resp, hwid_headers))


# Configured header text is bounded so a mistake in the environment cannot grow
# every subscription response; 512 bytes is far above any real title or notice.
_HEADER_TEXT_MAX = 512


def _client_ui_headers(days: int) -> dict[str, str]:
    """Headers the client app renders its own interface from.

    ``expire`` uses the same remaining-days arithmetic as the status remark, so
    a client that reads the header and one that reads the first VLESS line show
    the same term.  Zero days means "expired now", not "never expires": in this
    format ``expire=0`` is how unlimited is spelled, and an account with no
    balance is the opposite of unlimited.  ``days`` is already clamped at zero,
    which is what keeps an overdrawn account from producing a past timestamp.
    """
    settings = settings_relays()
    headers = {
        'subscription-userinfo':
            f'upload=0; download=0; total=0; expire={int(time.time()) + days * 86400}',
    }
    title = _header_text(getattr(settings, 'SUBSCRIPTION_PROFILE_TITLE', ''))
    if title:
        headers['profile-title'] = _base64_header(title)
    support_url = _header_url(getattr(settings, 'SUBSCRIPTION_SUPPORT_URL', ''))
    if support_url:
        headers['support-url'] = support_url
    # The bot is the only web destination this deployment has that is safe to
    # publish: a subscription URL is bearer access data and never leaves here.
    web_page_url = _header_url(getattr(settings, 'BOT_LINK', ''))
    if web_page_url:
        headers['profile-web-page-url'] = web_page_url
    announce = _header_text(getattr(settings, 'SUBSCRIPTION_ANNOUNCE_TEXT', ''))
    if announce:
        headers['announce'] = _base64_header(announce)
    return headers


def _header_text(value) -> str:
    """Return bounded configured text, or '' for anything unusable."""
    if not isinstance(value, str):
        return ''
    value = value.strip()
    return value if value and len(value) <= _HEADER_TEXT_MAX else ''


def _header_url(value) -> str:
    """Return a URL safe to place in a header verbatim, or ''.

    Django raises ``BadHeaderError`` on a newline, which would turn one stray
    character in the environment into a 500 on every subscription refresh.
    Dropping the header keeps the endpoint serving its actual purpose.
    """
    value = _header_text(value)
    if any(character < ' ' or character == '\x7f' for character in value):
        return ''
    return value


def _base64_header(text: str) -> str:
    """Display text travels as ``base64:<...>``.

    A header has no encoding of its own, so Russian copy and line breaks would
    otherwise be unrepresentable; the client decodes the payload itself.
    """
    return 'base64:' + base64.b64encode(text.encode('utf-8')).decode('ascii')


def _device_gate(request, user_vpn) -> tuple[bool, dict[str, str]]:
    """Decide whether this device may be served, and how to say so in headers."""
    headers = {'x-hwid-active': 'true'}
    hwid = client_hwid(request)
    if not hwid:
        # Clients that predate the header keep working until the fleet has
        # upgraded; strict mode is what makes refusing them a deliberate step.
        headers['x-hwid-not-supported'] = 'true'
        return not hwid_strict(), headers
    return register_device(user_vpn, hwid, client_metadata(request)), headers


def _refused(request) -> HttpResponse:
    """Return the one 404 this endpoint ever produces.

    Status, body and headers are derived from the request alone, so an unknown
    sub_id, a disabled subscription and a device this subscription will not bind
    are the same response.  Anything else would let a caller confirm that a
    guessed sub_id is real, on an endpoint whose id is the only secret.  The
    ``x-hwid-*`` flags stay because the client still has to render *some*
    reason, and now they say nothing about the subscription.
    """
    headers = {'x-hwid-active': 'true'}
    if client_hwid(request):
        headers['x-hwid-max-devices-reached'] = 'true'
        headers['x-hwid-limit'] = 'true'
    else:
        headers['x-hwid-not-supported'] = 'true'
    return _no_cache_response(_with_headers(HttpResponseNotFound(), headers))


def _with_headers(response: HttpResponse, headers: dict[str, str]) -> HttpResponse:
    for name, value in headers.items():
        response[name] = value
    return response


def _no_cache_response(response: HttpResponse) -> HttpResponse:
    """Prevent bearer subscription responses, including 404s, from being stored."""
    response['Cache-Control'] = 'private, no-store'
    response['Pragma'] = 'no-cache'
    return response


def _endpoint(client_vpn_host: str, default_port: int) -> tuple[str, int]:
    host, sep, port = client_vpn_host.rpartition(':')
    if sep and port.isdigit():
        return host, int(port)
    return client_vpn_host, default_port


def _is_backup_test_user(user_vpn_id: int) -> bool:
    from django.conf import settings
    if not getattr(settings, 'SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED', False):
        return False
    test_ids = getattr(settings, 'SUBSCRIPTION_BACKUP_TEST_USER_IDS', [])
    # Empty or malformed allowlists during rollout = no one receives backups.
    return isinstance(test_ids, list) and bool(test_ids) and user_vpn_id in test_ids


# Hard caps prevent a provider response or settings error from growing a normal
# subscription refresh without bound. Cache keys are digests, never bearer URLs.
_BACKUP_RESPONSE_HARD_MAX_BYTES = 1024 * 1024
_BACKUP_CACHE_HARD_MAX_ENTRIES = 32
_BACKUP_CACHE: dict[str, tuple[float, list[str]]] = {}
_BACKUP_CACHE_LOCK = threading.RLock()
_BACKUP_FETCHING: dict[str, threading.Event] = {}
_BACKUP_CACHE_GENERATION = 0


def _backup_links() -> list[str] | None:
    """Return a bounded, stable-deduplicated aggregate of opaque VLESS lines."""
    from django.conf import settings
    if not getattr(settings, 'SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED', False):
        _clear_backup_cache()
        return None
    urls = getattr(settings, 'SUBSCRIPTION_BACKUP_UPSTREAM_URLS', [])
    if not isinstance(urls, list):
        _clear_backup_cache()
        return None

    source_limit = int(_bounded_number(
        getattr(settings, 'SUBSCRIPTION_BACKUP_MAX_SOURCES', 8), default=8, lower=1, upper=32))
    valid_urls = [url for url in urls if isinstance(url, str) and _valid_upstream_url(url)][:source_limit]
    if not valid_urls:
        _clear_backup_cache()
        return None
    _evict_backup_cache({_backup_cache_key(url) for url in valid_urls})
    line_limit = int(_bounded_number(
        getattr(settings, 'SUBSCRIPTION_BACKUP_AGGREGATE_MAX_LINES', 256), default=256, lower=1, upper=2048))
    byte_limit = int(_bounded_number(
        getattr(settings, 'SUBSCRIPTION_BACKUP_AGGREGATE_MAX_BYTES', 262144), default=262144,
        lower=1, upper=_BACKUP_RESPONSE_HARD_MAX_BYTES))
    allowed_line_sha256 = getattr(settings, 'SUBSCRIPTION_BACKUP_ALLOWED_LINE_SHA256', None)
    if allowed_line_sha256 is not None and not _valid_line_sha256_allowlist(allowed_line_sha256):
        return None
    links, seen, total_bytes = [], set(), 0
    for url in valid_urls:
        for link in _cached_upstream_links(url):
            encoded = link.encode('utf-8')
            if (allowed_line_sha256 is not None
                    and hashlib.sha256(encoded).hexdigest() not in allowed_line_sha256):
                continue
            if link in seen or len(links) >= line_limit or total_bytes + len(encoded) > byte_limit:
                continue
            seen.add(link)
            links.append(link)
            total_bytes += len(encoded)
    return links or None


def _valid_line_sha256_allowlist(value) -> bool:
    """Accept only the exact lowercase SHA-256 digest schema from the secret mount."""
    return isinstance(value, list) and all(
        isinstance(digest, str)
        and len(digest) == 64
        and all(character in '0123456789abcdef' for character in digest)
        for digest in value
    )


def _backup_cache_key(url: str) -> str:
    return hashlib.sha256(url.encode('utf-8')).hexdigest()


def _clear_backup_cache() -> None:
    global _BACKUP_CACHE_GENERATION
    with _BACKUP_CACHE_LOCK:
        _BACKUP_CACHE.clear()
        _BACKUP_CACHE_GENERATION += 1


def _evict_backup_cache(active_keys: set[str]) -> None:
    now = time.monotonic()
    with _BACKUP_CACHE_LOCK:
        for key, (expiry, _links) in list(_BACKUP_CACHE.items()):
            if expiry <= now or key not in active_keys:
                _BACKUP_CACHE.pop(key, None)
        while len(_BACKUP_CACHE) > _BACKUP_CACHE_HARD_MAX_ENTRIES:
            _BACKUP_CACHE.pop(next(iter(_BACKUP_CACHE)), None)


def _cached_upstream_links(url: str) -> list[str]:
    """Fetch one upstream and cache only a current, validated result."""
    from django.conf import settings
    key = _backup_cache_key(url)
    now = time.monotonic()
    with _BACKUP_CACHE_LOCK:
        cached = _BACKUP_CACHE.get(key)
        if cached and cached[0] > now:
            return cached[1]
        if cached:
            _BACKUP_CACHE.pop(key, None)
        in_flight = _BACKUP_FETCHING.get(key)
        if in_flight is None:
            in_flight = threading.Event()
            _BACKUP_FETCHING[key] = in_flight
            generation = _BACKUP_CACHE_GENERATION
            fetcher = True
        else:
            fetcher = False

    if not fetcher:
        in_flight.wait()
        with _BACKUP_CACHE_LOCK:
            cached = _BACKUP_CACHE.get(key)
            return cached[1] if cached and cached[0] > time.monotonic() else []

    try:
        response_headers, payload = _fetch_upstream_payload(url)
        links = _sanitize_upstream_payload(payload, response_headers)
        if not links:
            return []
        ttl = _bounded_number(getattr(settings, 'SUBSCRIPTION_BACKUP_CACHE_TTL_SECONDS', 300),
                              default=300, lower=1, upper=3600)
        # Start TTL only after all network and payload validation has succeeded.
        with _BACKUP_CACHE_LOCK:
            if generation != _BACKUP_CACHE_GENERATION:
                return []
            _BACKUP_CACHE[key] = (time.monotonic() + ttl, links)
            active_keys = set(_BACKUP_CACHE)
        _evict_backup_cache(active_keys)
        return links
    except _UpstreamPlaceholderDocument:
        # Identified by a truncated digest of the URL: enough for an operator to
        # tell two configured sources apart, useless as the bearer URL itself.
        logger.warning(
            'subscription backup source %s served a client-identification placeholder '
            'instead of a configuration; check the configured client identity',
            key[:12])
        return []
    except (ValueError, UnicodeError, OSError):
        return []
    finally:
        with _BACKUP_CACHE_LOCK:
            _BACKUP_FETCHING.pop(key, None)
            in_flight.set()


def _is_public_unicast(address: str) -> bool:
    """Accept only ordinary public unicast addresses, never special ranges."""
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return not any((
        parsed.is_multicast,
        parsed.is_unspecified,
        parsed.is_loopback,
        parsed.is_link_local,
        parsed.is_private,
        parsed.is_reserved,
        not parsed.is_global,
    ))


_DNS_RESOLVER_COMMAND = ('getent',)


def _resolve_public_upstream(url: str, deadline: float) -> set[str]:
    """Resolve A/AAAA records in killable children within an absolute deadline.

    ``getent`` uses the host's configured NSS resolver but runs outside the web
    worker. ``subprocess.run(timeout=...)`` kills and reaps it on expiry, unlike
    an in-process ``socket.getaddrinfo`` call that cannot be cancelled safely.
    """
    try:
        parsed = urlsplit(url)
        host, port = parsed.hostname, parsed.port or 443
        addresses = set()
        for database, family in (('ahostsv4', 4), ('ahostsv6', 6)):
            result = subprocess.run(
                (*_DNS_RESOLVER_COMMAND, database, host),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=_remaining_timeout(deadline, maximum=60),
                check=False,
            )
            for line in result.stdout.splitlines():
                candidate = line.split(maxsplit=1)[0] if line else ''
                try:
                    address = ipaddress.ip_address(candidate)
                except ValueError:
                    continue
                if address.version == family:
                    addresses.add(str(address))
        if not addresses or any(not _is_public_unicast(address) for address in addresses):
            raise ValueError('unsafe_upstream_destination')
        return addresses
    except (TypeError, ValueError, OSError, subprocess.TimeoutExpired):
        raise ValueError('unsafe_upstream_destination') from None


def _chosen_upstream_ip(addresses: set[str]) -> str:
    """Select a validated DNS answer deterministically (IPv4 before IPv6)."""
    return min(addresses, key=lambda address: (ipaddress.ip_address(address).version,
                                                int(ipaddress.ip_address(address))))


def _remaining_timeout(deadline: float, maximum: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError('upstream_fetch_deadline')
    return min(maximum, remaining)


def _host_header(host: str, port: int) -> str:
    return host if port == 443 else f'{host}:{port}'


def _fetch_upstream_payload(url: str) -> tuple[dict[str, str], bytes]:
    """Fetch identity bytes over TLS pinned to one pre-resolved public IP.

    A single absolute monotonic deadline starts before DNS. Every blocking
    receive is bounded by the remaining time, so a slow-drip response cannot
    extend the request by repeatedly resetting a per-read timeout.

    Response headers travel back with the body because a provider says whether
    it accepted our client identity there, and a document alone cannot be told
    apart from the instructions it serves to a client it does not recognize.
    """
    from django.conf import settings
    deadline_seconds = _bounded_number(getattr(settings, 'SUBSCRIPTION_BACKUP_FETCH_DEADLINE_SECONDS', 8),
                                       default=8, lower=0.1, upper=60)
    deadline = time.monotonic() + deadline_seconds
    parsed = urlsplit(url)
    host, port = parsed.hostname, parsed.port or 443
    addresses = _resolve_public_upstream(url, deadline)
    destination = _chosen_upstream_ip(addresses)
    connect_timeout = _bounded_number(getattr(settings, 'SUBSCRIPTION_BACKUP_CONNECT_TIMEOUT_SECONDS', 3),
                                      default=3, lower=0.1, upper=30)
    read_timeout = _bounded_number(getattr(settings, 'SUBSCRIPTION_BACKUP_READ_TIMEOUT_SECONDS', 5),
                                   default=5, lower=0.1, upper=30)
    max_bytes = int(_bounded_number(getattr(settings, 'SUBSCRIPTION_BACKUP_RESPONSE_MAX_BYTES', 262144),
                                    default=262144, lower=1, upper=_BACKUP_RESPONSE_HARD_MAX_BYTES))
    target = parsed.path or '/'
    if parsed.query:
        target = f'{target}?{parsed.query}'
    raw_socket = tls_socket = None
    try:
        raw_socket = socket.create_connection(
            (destination, port), timeout=_remaining_timeout(deadline, connect_timeout))
        context = ssl.create_default_context()
        raw_socket.settimeout(_remaining_timeout(deadline, connect_timeout))
        tls_socket = context.wrap_socket(raw_socket, server_hostname=host)
        tls_socket.settimeout(_remaining_timeout(deadline, read_timeout))
        peer_ip = tls_socket.getpeername()[0]
        if (ipaddress.ip_address(peer_ip) != ipaddress.ip_address(destination)
                or not _is_public_unicast(peer_ip)):
            raise ValueError('upstream_peer_mismatch')

        identity = ''.join(
            f'{name}: {value}\r\n' for name, value in _upstream_client_headers())
        request = (
            f'GET {target} HTTP/1.1\r\n'
            f'Host: {_host_header(host, port)}\r\n'
            f'User-Agent: {_upstream_user_agent()}\r\n'
            f'{identity}'
            'Accept-Encoding: identity\r\n'
            'Connection: close\r\n\r\n'
        ).encode('ascii')
        tls_socket.sendall(request)
        status, headers, body = _read_upstream_response(
            tls_socket, deadline, read_timeout, max_bytes)
        if status != 200:
            raise ValueError('upstream_http_status')
        encoding = headers.get('content-encoding', '').strip().lower()
        if encoding not in ('', 'identity'):
            raise ValueError('upstream_compressed_response')
        if headers.get('transfer-encoding', '').strip().lower() not in ('', 'identity'):
            raise ValueError('upstream_unsupported_transfer_encoding')
        declared = headers.get('content-length')
        try:
            declared_size = int(declared) if declared is not None else None
        except (TypeError, ValueError):
            raise ValueError('upstream_response_too_large') from None
        if declared_size is not None and (declared_size < 0 or declared_size > max_bytes):
            raise ValueError('upstream_response_too_large')
        if len(body) > max_bytes:
            raise ValueError('upstream_response_too_large')
        if declared_size is not None and len(body) != declared_size:
            raise ValueError('upstream_incomplete_response')
        return headers, bytes(body)
    finally:
        if tls_socket is not None:
            tls_socket.close()
        elif raw_socket is not None:
            raw_socket.close()


def _read_upstream_response(socket_, deadline: float, read_timeout: float,
                            max_bytes: int) -> tuple[int, dict[str, str], bytearray]:
    """Read HTTP/1.1 with deadline-bounded single recv calls.

    HTTPResponse's buffered header parsing can issue several socket reads without
    an opportunity to recompute the absolute timeout. This small identity-only
    reader keeps that invariant for both headers and body.
    """
    raw = bytearray()
    header_limit = 64 * 1024
    while b'\r\n\r\n' not in raw:
        if len(raw) > header_limit:
            raise ValueError('upstream_headers_too_large')
        socket_.settimeout(_remaining_timeout(deadline, read_timeout))
        chunk = socket_.recv(8192)
        if not chunk:
            raise ValueError('upstream_incomplete_response')
        raw.extend(chunk)
    raw_headers, body = raw.split(b'\r\n\r\n', 1)
    try:
        lines = raw_headers.decode('iso-8859-1').split('\r\n')
        _protocol, status, _reason = lines[0].split(' ', 2)
        headers = {}
        for line in lines[1:]:
            name, value = line.split(':', 1)
            headers[name.casefold()] = value.strip()
    except (UnicodeDecodeError, ValueError):
        raise ValueError('upstream_invalid_response') from None

    declared = headers.get('content-length')
    try:
        expected_size = int(declared) if declared is not None else None
    except ValueError:
        raise ValueError('upstream_response_too_large') from None
    if expected_size is not None and (expected_size < 0 or expected_size > max_bytes):
        raise ValueError('upstream_response_too_large')
    while expected_size is None or len(body) < expected_size:
        socket_.settimeout(_remaining_timeout(deadline, read_timeout))
        chunk = socket_.recv(min(8192, max_bytes - len(body) + 1))
        if not chunk:
            break
        body.extend(chunk)
        if len(body) > max_bytes:
            raise ValueError('upstream_response_too_large')
    return int(status), headers, body


_DEFAULT_UPSTREAM_USER_AGENT = 'SPECIAL-subscription-backup/1'
# Optional device description, sent only alongside a usable identifier. Each is
# capped at the width the same header has on our own subscription endpoint.
_UPSTREAM_DEVICE_HEADERS = (
    ('x-device-os', 'SUBSCRIPTION_BACKUP_UPSTREAM_DEVICE_OS', 32),
    ('x-ver-os', 'SUBSCRIPTION_BACKUP_UPSTREAM_OS_VERSION', 32),
    ('x-device-model', 'SUBSCRIPTION_BACKUP_UPSTREAM_DEVICE_MODEL', 64),
)
# How a provider running the same device convention as this deployment says it
# did not accept our client identity.
_UPSTREAM_IDENTITY_REFUSED_HEADERS = ('x-hwid-not-supported', 'x-hwid-limit')
# Only transports that keep the client UUID off the wire may be advertised by
# default. Plain VLESS exposes it on every handshake and is trivially
# fingerprinted, which is why our own plaintext inbounds were withdrawn.
_MIRROR_SECURE_TRANSPORTS = ('reality', 'tls')
# A provider document is attacker-controlled input; parsing it must cost a
# bounded amount of work regardless of how many entries it declares. A real
# multi-region document carries roughly 80 servers, so the endpoint budget is
# the working capacity and the entry budget only bounds the parse: one document
# interleaves its servers with the selector groups that name their regions.
_MIRROR_MAX_ENDPOINTS = 128
_MIRROR_MAX_DOCUMENT_ENTRIES = 512
# Group types that a provider uses to collect one region's servers.
_MIRROR_GROUP_TYPES = ('selector', 'urltest')


class _UpstreamPlaceholderDocument(ValueError):
    """The provider answered a client it did not recognize, not our request.

    Distinct from an empty result on purpose: it means our own identification
    is wrong and an operator can fix it, while an empty result means the source
    has nothing this deployment will serve.
    """


def _upstream_client_headers() -> list[tuple[str, str]]:
    """Identify this installation to a provider as one supported client device.

    The identifier is deliberately one stable configured value for the whole
    installation rather than something derived per user or per request. A
    provider counts distinct identifiers against a device limit, so a value that
    changed per subscription refresh would read as a device flood and get this
    deployment limited or blocked; one value plus the response cache keeps our
    fetch rate at what a single client would produce. The value is configuration
    and never a constant here.

    An unset or malformed identifier sends no identity headers at all, and the
    device description rides along only with a usable identifier, because it
    describes that device and means nothing without it.
    """
    from django.conf import settings
    hwid = getattr(settings, 'SUBSCRIPTION_BACKUP_UPSTREAM_HWID', '')
    if not valid_hwid(hwid):
        return []
    headers = [('x-hwid', hwid)]
    for name, setting_name, limit in _UPSTREAM_DEVICE_HEADERS:
        value = _upstream_header_value(getattr(settings, setting_name, ''), limit)
        if value:
            headers.append((name, value))
    return headers


def _upstream_header_value(value, limit: int) -> str:
    """Return a bounded printable-ASCII header value, or '' for anything else."""
    if not isinstance(value, str):
        return ''
    value = value.strip()
    if not value or len(value) > limit or not all(' ' <= character <= '~' for character in value):
        return ''
    return value


def _upstream_identity_refused(headers: dict[str, str]) -> bool:
    return any(str(headers.get(name, '')).strip().casefold() == 'true'
               for name in _UPSTREAM_IDENTITY_REFUSED_HEADERS)


def _upstream_user_agent() -> str:
    """Return the agent this deployment presents to providers.

    Some providers serve a different document per client User-Agent and refuse
    unknown ones, so the agent is what selects the machine-readable format. An
    unset, oversized or non-printable value keeps the neutral default rather
    than letting configuration write arbitrary bytes into a request header.
    """
    from django.conf import settings
    value = getattr(settings, 'SUBSCRIPTION_BACKUP_UPSTREAM_USER_AGENT', '')
    if not isinstance(value, str):
        return _DEFAULT_UPSTREAM_USER_AGENT
    value = value.strip()
    if not value or len(value) > 128 or not all(' ' <= character <= '~' for character in value):
        return _DEFAULT_UPSTREAM_USER_AGENT
    return value


def _sanitize_upstream_payload(payload: bytes, headers: dict[str, str] | None = None) -> list[str]:
    """Decode payload framing while retaining accepted VLESS line bytes exactly."""
    structured = _structured_upstream_links(payload, headers)
    if structured is not None:
        return structured
    decoded = _decode_subscription_payload(payload)
    links = []
    for raw_line in decoded.splitlines():
        if not raw_line.startswith(b'vless://') or _is_sentinel_vless_line(raw_line):
            continue
        links.append(raw_line.decode('utf-8'))
    return links


def _decode_subscription_payload(payload: bytes) -> bytes:
    compact = b''.join(payload.split())
    if not compact:
        raise ValueError('empty_upstream_payload')
    try:
        return base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError):
        return payload


def _structured_upstream_links(payload: bytes,
                               headers: dict[str, str] | None = None) -> list[str] | None:
    """Render links from a JSON provider document, or decline the payload.

    ``None`` means "not a document I parse" and hands the bytes back to the
    opaque URI-list path unchanged; an empty list means the document parsed and
    offered nothing servable. Callers must keep that distinction, otherwise a
    provider that answers YAML would silently look like a provider with no
    endpoints.

    Response headers are optional because the same parse runs on bytes alone in
    tests and on the opaque path. When they are present they decide one further
    case: a document that is really a message to the user, which must not be
    counted as a source with nothing to offer.
    """
    document = _load_upstream_json(payload)
    if document is None:
        return None
    raw_endpoints = []
    if isinstance(document, dict):
        raw_endpoints = _singbox_endpoints(document)
    else:
        # v2rayNG/Happ answer with an array of whole client configs rather than
        # one config holding every outbound.
        for element in document[:_MIRROR_MAX_DOCUMENT_ENTRIES]:
            if isinstance(element, dict):
                raw_endpoints.extend(_v2ray_endpoints(element))
            if len(raw_endpoints) >= _MIRROR_MAX_ENDPOINTS:
                break
    if headers and _is_identity_placeholder(raw_endpoints, headers):
        raise _UpstreamPlaceholderDocument('upstream_client_identity_placeholder')
    allow_plaintext = _plaintext_endpoints_allowed()
    links = []
    for raw_endpoint in raw_endpoints[:_MIRROR_MAX_ENDPOINTS]:
        endpoint = _normalized_mirror_endpoint(raw_endpoint)
        if endpoint is None:
            continue
        if endpoint['security'] not in _MIRROR_SECURE_TRANSPORTS and not allow_plaintext:
            continue
        links.append(_build_mirror_vless(endpoint))
    return links


def _is_identity_placeholder(raw_endpoints: list[dict], headers: dict[str, str]) -> bool:
    """Whether this document is the provider's "unsupported client" message.

    Two independent signals must agree: the provider says it did not accept our
    identity, and nothing it sent protects a handshake. Either alone is
    ordinary — a provider may refuse the identity and still serve real Reality
    endpoints, and a provider may genuinely offer only plaintext. Classifying on
    the parsed outbounds rather than the normalized ones keeps the verdict
    intact when the placeholder points at hosts we would drop anyway.
    """
    if not raw_endpoints or not _upstream_identity_refused(headers):
        return False
    return all(endpoint.get('security') not in _MIRROR_SECURE_TRANSPORTS
               for endpoint in raw_endpoints)


def _load_upstream_json(payload: bytes):
    """Return a parsed JSON container, or None for any other payload shape."""
    stripped = payload.lstrip()
    if stripped[:1] not in (b'{', b'['):
        return None
    try:
        document = json.loads(stripped.decode('utf-8'))
    except (UnicodeDecodeError, ValueError, RecursionError):
        # Deeply nested input makes recursive-descent json raise RecursionError,
        # which is not a ValueError and would otherwise escape the caller's
        # payload error handling on interpreters whose parser still recurses.
        return None
    return document if isinstance(document, (dict, list)) else None


def _plaintext_endpoints_allowed() -> bool:
    from django.conf import settings
    return getattr(settings, 'SUBSCRIPTION_BACKUP_ALLOW_PLAINTEXT_ENDPOINTS', False) is True


def _dict_field(container: dict, name: str) -> dict:
    value = container.get(name)
    return value if isinstance(value, dict) else {}


def _singbox_endpoints(document: dict) -> list[dict]:
    """Extract VLESS outbounds from a sing-box config."""
    outbounds = document.get('outbounds')
    if not isinstance(outbounds, list):
        return []
    entries = [outbound for outbound in outbounds[:_MIRROR_MAX_DOCUMENT_ENTRIES]
               if isinstance(outbound, dict)]
    regions = _singbox_regions(entries)
    endpoints = []
    for outbound in entries:
        if str(outbound.get('type', '')).lower() != 'vless':
            continue
        if len(endpoints) >= _MIRROR_MAX_ENDPOINTS:
            break
        tls = _dict_field(outbound, 'tls')
        reality = _dict_field(tls, 'reality')
        transport = _dict_field(outbound, 'transport')
        tag = outbound.get('tag')
        endpoints.append({
            'host': outbound.get('server'),
            'port': outbound.get('server_port'),
            'uuid': outbound.get('uuid'),
            'remark': tag,
            'region': regions.get(tag) if isinstance(tag, str) else None,
            'flow': outbound.get('flow'),
            'security': _singbox_security(tls, reality),
            'public_key': reality.get('public_key'),
            'short_id': reality.get('short_id'),
            'server_name': tls.get('server_name'),
            'fingerprint': _dict_field(tls, 'utls').get('fingerprint'),
            'network': transport.get('type') or 'tcp',
            'service_name': transport.get('service_name'),
            'path': transport.get('path'),
        })
    return endpoints


def _singbox_regions(outbounds: list[dict]) -> dict[str, str]:
    """Map each server tag to the group tag that names its region.

    A provider document lists one group per region holding that region's server
    tags, and a root group holding the region groups. Only a group that names a
    server directly labels it, which is what keeps the root group — whose
    members are other groups — from becoming every endpoint's region.
    """
    regions: dict[str, str] = {}
    for outbound in outbounds:
        if str(outbound.get('type', '')).lower() not in _MIRROR_GROUP_TYPES:
            continue
        label, members = outbound.get('tag'), outbound.get('outbounds')
        if not isinstance(label, str) or not isinstance(members, list):
            continue
        for member in members[:_MIRROR_MAX_DOCUMENT_ENTRIES]:
            if isinstance(member, str) and member not in regions:
                regions[member] = label
    return regions


def _singbox_security(tls: dict, reality: dict) -> str:
    """Classify a sing-box outbound by what actually protects the handshake."""
    if reality.get('enabled') is True:
        return 'reality'
    return 'tls' if tls.get('enabled') is True else 'none'


def _v2ray_endpoints(config: dict) -> list[dict]:
    """Extract VLESS servers from one v2ray client config."""
    outbounds = config.get('outbounds')
    if not isinstance(outbounds, list):
        return []
    endpoints = []
    for outbound in outbounds[:_MIRROR_MAX_ENDPOINTS]:
        if not isinstance(outbound, dict) or str(outbound.get('protocol', '')).lower() != 'vless':
            continue
        vnext = _dict_field(outbound, 'settings').get('vnext')
        if not isinstance(vnext, list):
            continue
        stream = _dict_field(outbound, 'streamSettings')
        security = str(stream.get('security') or 'none').lower()
        reality_settings = _dict_field(stream, 'realitySettings')
        # Reality and TLS keep their server name and fingerprint in different
        # blocks; reading the wrong one would advertise an empty SNI.
        secure = reality_settings if security == 'reality' else _dict_field(stream, 'tlsSettings')
        for server in vnext[:_MIRROR_MAX_ENDPOINTS]:
            if not isinstance(server, dict):
                continue
            users = server.get('users')
            user = users[0] if isinstance(users, list) and users and isinstance(users[0], dict) else {}
            endpoints.append({
                'host': server.get('address'),
                'port': server.get('port'),
                'uuid': user.get('id'),
                'remark': config.get('remarks'),
                'flow': user.get('flow'),
                'security': security,
                'public_key': reality_settings.get('publicKey'),
                'short_id': reality_settings.get('shortId'),
                'server_name': secure.get('serverName'),
                'fingerprint': secure.get('fingerprint'),
                'network': stream.get('network') or 'tcp',
                'service_name': _dict_field(stream, 'grpcSettings').get('serviceName'),
                'path': _dict_field(stream, 'wsSettings').get('path'),
            })
    return endpoints


def _normalized_mirror_endpoint(raw: dict) -> dict | None:
    """Normalize one parsed outbound, or drop it.

    Every value here came from a third party, so each one is length-bounded and
    stripped of control characters before it can reach a rendered URI.
    """
    host = _mirror_field(raw.get('host'), limit=253)
    port = _mirror_port(raw.get('port'))
    uuid = _mirror_field(raw.get('uuid'))
    security = raw.get('security')
    if (not host or not _safe_endpoint_host(host) or port is None or not uuid
            or security not in (*_MIRROR_SECURE_TRANSPORTS, 'none')):
        return None
    # The provider's own region label is the only thing that tells a user Japan
    # from Germany once several regions share a subscription. Both halves are
    # bounded and control-character-free before they meet, and the composed
    # remark is bounded again, so a label can lengthen a remark but never
    # replace the URI structure around it.
    remark = _mirror_field(raw.get('remark')) or host
    region = _mirror_field(raw.get('region'), limit=64)
    if region and region not in remark:
        remark = _mirror_field(f'{region} · {remark}') or remark
    endpoint = {
        'host': host,
        'port': port,
        # Our own UUIDs are quote-invariant, so quoting costs nothing here and
        # stops a provider from writing URI structure into the userinfo field.
        'uuid': quote(uuid, safe=''),
        'remark': remark,
        'flow': _mirror_field(raw.get('flow'), limit=64),
        'security': security,
        'public_key': _mirror_field(raw.get('public_key')),
        'short_id': _mirror_field(raw.get('short_id'), limit=64),
        'server_name': _mirror_field(raw.get('server_name'), limit=253),
        'fingerprint': _mirror_field(raw.get('fingerprint'), limit=64),
        'network': _mirror_field(raw.get('network'), limit=32).lower() or 'tcp',
        'service_name': _mirror_field(raw.get('service_name')),
        'path': _mirror_field(raw.get('path')),
    }
    # Reality without its key material is not a usable endpoint, and rendering
    # it would advertise a Reality link that no client can complete.
    if security == 'reality' and not (endpoint['public_key'] and endpoint['short_id']):
        return None
    return endpoint


def _mirror_field(value, *, limit: int = 256) -> str:
    """Return a bounded, control-character-free string, or '' for anything else."""
    if not isinstance(value, str) or not value or len(value) > limit:
        return ''
    if any(character < ' ' or character == '\x7f' for character in value):
        return ''
    return value


def _mirror_port(value) -> int | None:
    # ``type(...) is int`` deliberately rejects booleans, which JSON allows here.
    if type(value) is int and 0 < value < 65536:
        return value
    if isinstance(value, str) and value.isdigit() and 0 < int(value) < 65536:
        return int(value)
    return None


def _safe_endpoint_host(host: str) -> bool:
    """Reject hosts that would aim a client at this deployment's own network."""
    if any(character in host for character in ' \t/@?#') or host.casefold() == 'localhost':
        return False
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        return True
    return _is_public_unicast(str(literal))


def _build_mirror_vless(endpoint: dict) -> str:
    """Render a normalized third-party endpoint in the URI shape we emit.

    ``_build_vless`` cannot be reused: it hardcodes ``security=reality`` and
    dereferences Reality-only fields, while a mirrored endpoint may carry plain
    TLS or nothing at all. Shared fields keep its ordering so an aggregated
    subscription reads as one document.
    """
    reality = endpoint['security'] == 'reality'
    query_fields = [('type', endpoint['network']), ('security', endpoint['security'])]
    if endpoint['flow']:
        query_fields.insert(0, ('flow', endpoint['flow']))
    if reality:
        query_fields.append(('pbk', endpoint['public_key']))
    if endpoint['fingerprint']:
        query_fields.append(('fp', endpoint['fingerprint']))
    if endpoint['server_name']:
        query_fields.append(('sni', endpoint['server_name']))
    if reality:
        query_fields.extend((('sid', endpoint['short_id']), ('spx', '/')))
    if endpoint['network'] == 'grpc' and endpoint['service_name']:
        query_fields.append(('serviceName', endpoint['service_name']))
    if endpoint['network'] == 'ws' and endpoint['path']:
        query_fields.append(('path', endpoint['path']))
    query = urlencode(query_fields, quote_via=quote)
    return (f"vless://{endpoint['uuid']}@{endpoint['host']}:{endpoint['port']}"
            f"?{query}#{quote(endpoint['remark'])}")


def _is_sentinel_vless_line(raw_line: bytes) -> bool:
    """Reject only exact, normalized marker remarks and unsafe literal hosts."""
    try:
        parsed = urlsplit(raw_line.decode('utf-8'))
        host = parsed.hostname
        if not host or host.lower() == 'localhost':
            return True
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal and (literal.is_private or literal.is_loopback or literal.is_unspecified):
            return True
        fragment = unquote_to_bytes(parsed.fragment).decode('utf-8').strip().casefold()
    except (UnicodeDecodeError, ValueError):
        return True
    return fragment in {'dummy', 'expired', 'non-working', 'nonworking', 'subscription expired'}


def _valid_upstream_url(url: str) -> bool:
    """Accept only allowlisted HTTPS DNS names with a valid optional port."""
    from django.conf import settings
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
                or parsed.password or parsed.fragment):
            return False
        # Access deliberately validates malformed and out-of-range ports.
        parsed.port
        try:
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            pass
        else:
            return False
        allowed_hosts = getattr(settings, 'SUBSCRIPTION_BACKUP_UPSTREAM_HOSTS', None)
        if allowed_hosts is not None:
            if not isinstance(allowed_hosts, list):
                return False
            return parsed.hostname.casefold() in {
                item.casefold() for item in allowed_hosts if isinstance(item, str)
            }
        return True
    except (TypeError, ValueError):
        return False


def _bounded_number(value, *, default: float, lower: float, upper: float) -> float:
    try:
        return min(max(float(value), lower), upper)
    except (TypeError, ValueError):
        return default


def settings_relays():
    from django.conf import settings
    return settings