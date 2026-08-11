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
import socket
import ssl
import subprocess
import threading
import time
from functools import lru_cache
from urllib.parse import unquote_to_bytes, urlsplit

from django.http import HttpResponse, HttpResponseNotFound
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from apps.servers.models import Server
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN
from utils.py3xui.async_api import AsyncApi


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


def _build_vless(uuid: str, host: str, port: int, remark: str, params: dict, flow: str = '',
                  fingerprint: str = 'chrome') -> str:
    from urllib.parse import quote
    network = params.get('network', 'tcp')
    query = (
        f"type={network}&security=reality&pbk={params['public_key']}"
        f"&fp={fingerprint}&sni={params['server_name']}&sid={params['short_ids'][0]}&spx=%2F"
    )
    if flow:
        query = f"flow={flow}&" + query
    return f"vless://{uuid}@{host}:{port}?{query}#{quote(remark)}"


@csrf_exempt
@require_GET
def subscription_proxy(request, sub_id: str):
    try:
        user_vpn = UserVPN.objects.select_related('server', 'user').get(sub_id=sub_id)
    except UserVPN.DoesNotExist:
        return _no_cache_response(HttpResponseNotFound())

    if not user_vpn.enabled:
        return _no_cache_response(HttpResponseNotFound())

    server = user_vpn.server
    params = _get_params(server.id, server.inbound_id)

    # Balance / remaining days for the status remark.
    user = TelegramUser.objects.annotate_balance().filter(id=user_vpn.user_id).first()
    price = float(server.tariff.price) if server.tariff else 0.0
    balance = float(getattr(user, 'balance', 0) or 0) if user else 0.0
    days = int(balance // price) if price > 0 else 0
    status_label = f'осталось {days} дней' if days > 0 else 'подписка окончена'

    # Client endpoint hosts.
    # Direct = public NL sub domain on the inbound port.
    # Relay  = the client_vpn_host stored on the server (e.g. the RU relay front).
    relay_host, relay_port = _endpoint(server.client_vpn_host, params['port'])
    sub_domain = settings_relays().SUBSCRIPTION_BASE_URL.split('/')[2].split(':')[0]  # hostname only
    direct_host = sub_domain
    direct_port = params['port']

    uuid_str = str(user_vpn.vpn_uuid)
    # Preserve the deployed legacy client contract. Most existing control-plane
    # clients have no Vision flow, and forcing it in the generated subscription
    # makes those links intermittently land on an incompatible same-port
    # listener. Vision may be promoted only by an explicit per-client migration.
    flow = ''

    links = []
    # 1) Status entry (non-working) first, matching the happ UX.
    links.append(_build_vless(uuid_str, '127.0.0.1', 1, f'📊 Подписка-{status_label}', params, flow=''))
    # 2) Direct NL primary.
    links.append(_build_vless(uuid_str, direct_host, direct_port, '🇳🇱 NL Direct', params, flow=flow))
    # 3) External backup endpoints (feature-gated test group).
    backup_links = _backup_links() if _is_backup_test_user(user_vpn.id) else None
    if backup_links:
        links.extend(backup_links)
    # 4) RU relay (only if configured).
    if relay_host:
        links.append(_build_vless(uuid_str, relay_host, relay_port, '🇳🇱 NL Relay', params, flow=flow))

    payload = '\n'.join(links) + '\n'
    encoded = base64.b64encode(payload.encode('utf-8'))
    resp = HttpResponse(encoded, content_type='text/plain')
    resp['Profile-Update-Interval'] = '12'
    resp['Subscription-Userinfo'] = f'upload=0; download=0; total=0; expire=0'
    return _no_cache_response(resp)


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
        getattr(settings, 'SUBSCRIPTION_BACKUP_AGGREGATE_MAX_LINES', 128), default=128, lower=1, upper=2048))
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
        links = _sanitize_upstream_payload(_fetch_upstream_payload(url))
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


def _fetch_upstream_payload(url: str) -> bytes:
    """Fetch identity bytes over TLS pinned to one pre-resolved public IP.

    A single absolute monotonic deadline starts before DNS. Every blocking
    receive is bounded by the remaining time, so a slow-drip response cannot
    extend the request by repeatedly resetting a per-read timeout.
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

        request = (
            f'GET {target} HTTP/1.1\r\n'
            f'Host: {_host_header(host, port)}\r\n'
            'User-Agent: SPECIAL-subscription-backup/1\r\n'
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
        return bytes(body)
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


def _sanitize_upstream_payload(payload: bytes) -> list[str]:
    """Decode payload framing while retaining accepted VLESS line bytes exactly."""
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