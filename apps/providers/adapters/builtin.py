from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import asdict
from datetime import UTC, datetime
from urllib.parse import parse_qsl, unquote, urlsplit
from uuid import UUID

from django.conf import settings
from django.utils import timezone

from apps.providers.adapters.base import (
    ProviderAdapter,
    ProviderCapabilities,
    ProviderConfigurationError,
    ProviderInventory,
)
from apps.providers.adapters.registry import register_adapter
from apps.providers.http import ProviderHTTPResponse, fetch_https, valid_public_host
from apps.providers.sources import ProviderSource, load_provider_source
from apps.providers.types import CanonicalEndpoint


_MAX_LINES = 512
_MAX_LINE_BYTES = 4096
_MAX_PAYLOAD_BYTES = 1024 * 1024
_SUPPORTED_TRANSPORTS = frozenset({'tcp', 'grpc', 'ws', 'xhttp'})
_SUPPORTED_SECURITY = frozenset({'reality', 'tls'})
_BASE64_RE = re.compile(rb'^[A-Za-z0-9+/_-]+={0,2}$')
_INSECURE_KEYS = frozenset({
    'allow_insecure',
    'allowinsecure',
    'insecure',
    'skip-cert-verify',
    'tlsinsecure',
})
_BUILTINS_REGISTERED = False


def _strict_query(raw_query: str) -> dict[str, str] | None:
    query = {}
    for key, value in parse_qsl(raw_query, keep_blank_values=True):
        normalized = key.casefold()
        if not normalized or normalized in query:
            return None
        query[normalized] = value
    return query


def _bounded_field(value: str, limit: int) -> str | None:
    if not isinstance(value, str) or len(value) > limit:
        return None
    if any(character in value for character in '\r\n\t'):
        return None
    return value


def _region_from_label(label: str) -> str:
    previous = ''
    for character in label:
        index = ord(character) - 0x1F1E6
        letter = chr(ord('a') + index) if 0 <= index < 26 else ''
        if letter and previous:
            return previous + letter
        previous = letter
    for token in label.replace('-', ' ').replace('_', ' ').split():
        if len(token) == 2 and token.isascii() and token.isupper():
            return token.casefold()
    return ''


def _decode_lines(payload: bytes) -> bytes:
    if not isinstance(payload, bytes) or len(payload) > _MAX_PAYLOAD_BYTES:
        raise ProviderConfigurationError('provider_payload_too_large')
    compact = b''.join(payload.split())
    if not compact:
        raise ProviderConfigurationError('provider_payload_empty')
    if not _BASE64_RE.fullmatch(compact):
        return payload
    padded = compact + b'=' * (-len(compact) % 4)
    try:
        decoded = base64.b64decode(padded, altchars=b'-_', validate=True)
    except (binascii.Error, ValueError):
        return payload
    if len(decoded) > _MAX_PAYLOAD_BYTES:
        raise ProviderConfigurationError('provider_payload_too_large')
    return decoded


def _endpoint_id(provider_slug: str, fields: dict[str, object]) -> str:
    digest = hashlib.sha256(
        json.dumps(fields, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).hexdigest()[:32]
    return f'{provider_slug}-{digest}'


def _parse_vless_line(raw_line: bytes, provider_slug: str) -> CanonicalEndpoint | None:
    if not raw_line.startswith(b'vless://') or len(raw_line) > _MAX_LINE_BYTES:
        return None
    try:
        line = raw_line.decode('utf-8')
        parsed = urlsplit(line)
        query = _strict_query(parsed.query)
        port = parsed.port
        UUID(parsed.username)
    except (UnicodeDecodeError, TypeError, ValueError):
        return None
    if (
        query is None
        or parsed.scheme != 'vless'
        or parsed.password is not None
        or not parsed.hostname
        or port is None
        or not valid_public_host(parsed.hostname)
        or any(key in query for key in _INSECURE_KEYS)
        or query.get('encryption', 'none').casefold() != 'none'
    ):
        return None
    security = query.get('security', 'none').casefold()
    transport = query.get('type', 'tcp').casefold()
    if security not in _SUPPORTED_SECURITY or transport not in _SUPPORTED_TRANSPORTS:
        return None
    server_name = query.get('sni', '')
    public_key = query.get('pbk', '')
    if security == 'reality' and (not valid_public_host(server_name) or not public_key):
        return None
    if security == 'tls' and not valid_public_host(server_name):
        return None
    fields = {
        'protocol': 'vless',
        'transport': transport,
        'security': security,
        'host': parsed.hostname.casefold(),
        'port': port,
        'region': _region_from_label(unquote(parsed.fragment)),
        'server_name': server_name.casefold(),
        'path': query.get('path', ''),
        'service_name': query.get('servicename', ''),
        'public_key': public_key,
        'short_id': query.get('sid', ''),
        'fingerprint': query.get('fp', ''),
        'flow': query.get('flow', ''),
        'host_header': query.get('host', ''),
        'transport_mode': query.get('mode', ''),
        'alpn': query.get('alpn', ''),
    }
    limits = {
        'path': 512,
        'service_name': 255,
        'public_key': 128,
        'short_id': 32,
        'fingerprint': 32,
        'flow': 64,
        'host_header': 253,
        'transport_mode': 32,
        'alpn': 128,
    }
    for field_name, limit in limits.items():
        bounded = _bounded_field(fields[field_name], limit)
        if bounded is None:
            return None
        fields[field_name] = bounded
    if fields['path'] and not fields['path'].startswith('/'):
        return None
    if fields['host_header'] and not valid_public_host(fields['host_header']):
        return None
    if transport == 'grpc' and fields['transport_mode'].casefold() not in {'', 'gun', 'multi'}:
        return None
    identity_fields = {key: value for key, value in fields.items() if key != 'region'}
    external_id = _endpoint_id(provider_slug, identity_fields)
    return CanonicalEndpoint(external_id=external_id, **fields)


def parse_vless_subscription(payload: bytes, provider_slug: str) -> tuple[CanonicalEndpoint, ...]:
    decoded = _decode_lines(payload).lstrip(b'\xef\xbb\xbf')
    raw_lines = decoded.splitlines()
    if len(raw_lines) > _MAX_LINES:
        raise ProviderConfigurationError('provider_payload_too_many_lines')
    endpoints = {}
    for raw_line in raw_lines:
        endpoint = _parse_vless_line(raw_line.strip(), provider_slug)
        if endpoint is not None:
            endpoints.setdefault(endpoint.external_id, endpoint)
    return tuple(sorted(endpoints.values(), key=lambda endpoint: endpoint.external_id))


def _normalized_digest(endpoints: tuple[CanonicalEndpoint, ...]) -> str:
    return hashlib.sha256(
        json.dumps(
            [asdict(endpoint) for endpoint in endpoints],
            sort_keys=True,
            separators=(',', ':'),
        ).encode('utf-8')
    ).hexdigest()


def _expiry_from_headers(headers: dict[str, str], fetched_at: datetime) -> datetime | None:
    value = headers.get('subscription-userinfo', '')
    fields = {}
    for item in value.split(';'):
        key, separator, raw = item.strip().partition('=')
        if separator:
            fields[key.casefold()] = raw
    try:
        timestamp = int(fields.get('expire', '0'))
    except ValueError:
        return None
    if timestamp <= 0:
        return None
    expires_at = datetime.fromtimestamp(timestamp, tz=UTC)
    return expires_at if expires_at > fetched_at else None


def _fetch(
    source: ProviderSource,
    *,
    url: str,
    allow_follow_hosts: bool,
    subscription_request: bool,
    authorization: str,
) -> ProviderHTTPResponse:
    allowed_hosts = (
        frozenset({source.host})
        if not allow_follow_hosts
        else frozenset({source.host, *source.follow_hosts})
    )
    user_agent = source.subscription_user_agent if subscription_request else source.user_agent
    return fetch_https(
        url,
        allowed_hosts=allowed_hosts,
        user_agent=user_agent,
        authorization=authorization,
        connect_timeout=getattr(settings, 'PROVIDER_FETCH_CONNECT_TIMEOUT_SECONDS', 3),
        read_timeout=getattr(settings, 'PROVIDER_FETCH_READ_TIMEOUT_SECONDS', 5),
        deadline_seconds=getattr(settings, 'PROVIDER_FETCH_DEADLINE_SECONDS', 10),
        max_bytes=getattr(settings, 'PROVIDER_FETCH_MAX_BYTES', 1024 * 1024),
    )


def _vpnstar_subscription_url(response: ProviderHTTPResponse) -> str:
    try:
        document = json.loads(response.body)
    except (UnicodeError, json.JSONDecodeError):
        raise ProviderConfigurationError('vpnstar_connection_link_invalid') from None
    if not isinstance(document, dict):
        raise ProviderConfigurationError('vpnstar_connection_link_invalid')
    if isinstance(document.get('data'), dict):
        document = document['data']
    for key in ('subscription_url', 'display_link', 'fallback_url'):
        value = document.get(key)
        if isinstance(value, str) and value.startswith('https://'):
            return value
    raise ProviderConfigurationError('vpnstar_connection_link_invalid')


class _SharedSubscriptionAdapter(ProviderAdapter):
    adapter_name = ''

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(credential_mode='shared', protocols=('vless',))

    def _payload(self, source: ProviderSource) -> ProviderHTTPResponse:
        raise NotImplementedError

    def fetch_inventory(self) -> ProviderInventory:
        source = load_provider_source(self.provider.slug, self.adapter_name)
        response = self._payload(source)
        endpoints = parse_vless_subscription(response.body, self.provider.slug)
        if not endpoints:
            raise ProviderConfigurationError('provider_inventory_empty')
        fetched_at = timezone.now()
        return ProviderInventory(
            source_digest=source.source_digest,
            payload_digest=_normalized_digest(endpoints),
            endpoints=endpoints,
            fetched_at=fetched_at,
            expires_at=_expiry_from_headers(response.headers, fetched_at),
        )


class AServiceAdapter(_SharedSubscriptionAdapter):
    adapter_name = 'a-service'

    def _payload(self, source: ProviderSource) -> ProviderHTTPResponse:
        return _fetch(
            source,
            url=source.url,
            allow_follow_hosts=False,
            subscription_request=True,
            authorization=source.authorization,
        )


class VPNStarAdapter(_SharedSubscriptionAdapter):
    adapter_name = 'vpnstar'

    def _payload(self, source: ProviderSource) -> ProviderHTTPResponse:
        response = _fetch(
            source,
            url=source.url,
            allow_follow_hosts=False,
            subscription_request=source.kind == 'subscription',
            authorization=source.authorization,
        )
        if source.kind == 'subscription':
            return response
        subscription_url = _vpnstar_subscription_url(response)
        return _fetch(
            source,
            url=subscription_url,
            allow_follow_hosts=True,
            subscription_request=True,
            authorization='',
        )


def register_builtin_adapters() -> None:
    global _BUILTINS_REGISTERED
    if _BUILTINS_REGISTERED:
        return
    register_adapter('a-service', AServiceAdapter)
    register_adapter('vpnstar', VPNStarAdapter)
    _BUILTINS_REGISTERED = True
