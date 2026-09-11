from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings

from apps.providers.adapters.base import ProviderConfigurationError
from apps.providers.http import valid_public_host


_MAX_SECRET_FILE_BYTES = 256 * 1024
_MAX_PROVIDERS = 32
_PROVIDER_ID_RE = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$')
_ALLOWED_FIELDS = frozenset({
    'adapter',
    'authorization',
    'enabled',
    'follow_hosts',
    'id',
    'kind',
    'subscription_user_agent',
    'url',
    'user_agent',
})


@dataclass(frozen=True)
class ProviderSource:
    provider_id: str
    adapter: str
    kind: str
    url: str
    host: str
    follow_hosts: frozenset[str]
    user_agent: str
    subscription_user_agent: str
    enabled: bool = True
    authorization: str = ''

    @property
    def source_digest(self) -> str:
        document = {
            'adapter': self.adapter,
            'authorization': self.authorization,
            'enabled': self.enabled,
            'follow_hosts': sorted(self.follow_hosts),
            'id': self.provider_id,
            'kind': self.kind,
            'subscription_user_agent': self.subscription_user_agent,
            'url': self.url,
            'user_agent': self.user_agent,
        }
        payload = json.dumps(document, sort_keys=True, separators=(',', ':')).encode('utf-8')
        return hmac.new(settings.SECRET_KEY.encode('utf-8'), payload, hashlib.sha256).hexdigest()


def _valid_agent(value) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 128
        and all(' ' <= character <= '~' for character in value)
    )


def _valid_authorization(value) -> bool:
    return (
        value == ''
        or (
            isinstance(value, str)
            and value.startswith('Bearer ')
            and len(value) > len('Bearer ')
            and len(value) <= 2048
            and all(' ' <= character <= '~' for character in value)
        )
    )


def _source_from_document(item: dict) -> ProviderSource:
    if not isinstance(item, dict) or set(item) - _ALLOWED_FIELDS:
        raise ProviderConfigurationError('provider_source_schema_invalid')
    provider_id = item.get('id')
    adapter = item.get('adapter')
    kind = item.get('kind')
    url = item.get('url')
    enabled = item.get('enabled', False)
    user_agent = item.get('user_agent', 'SPECIAL-provider-ingest/1')
    subscription_user_agent = item.get('subscription_user_agent', 'v2rayNG/1.8.5')
    authorization = item.get('authorization', '')
    follow_hosts = item.get('follow_hosts', [])
    if (
        not isinstance(provider_id, str)
        or not _PROVIDER_ID_RE.fullmatch(provider_id)
        or adapter not in {'a-service', 'vpnstar'}
        or kind not in {'subscription', 'connection_link'}
        or type(enabled) is not bool
        or not isinstance(url, str)
        or not 1 <= len(url) <= 4096
        or not url.isascii()
        or not all('!' <= character <= '~' for character in url)
        or '#' in url
        or not _valid_agent(user_agent)
        or not _valid_agent(subscription_user_agent)
        or not _valid_authorization(authorization)
        or not isinstance(follow_hosts, list)
        or len(follow_hosts) > 16
        or any(not valid_public_host(host) for host in follow_hosts)
    ):
        raise ProviderConfigurationError('provider_source_schema_invalid')
    if adapter == 'a-service' and kind != 'subscription':
        raise ProviderConfigurationError('provider_source_kind_invalid')
    try:
        parsed = urlsplit(url)
        port = parsed.port or 443
    except ValueError:
        raise ProviderConfigurationError('provider_source_url_invalid') from None
    host = parsed.hostname.casefold() if parsed.hostname else ''
    if (
        parsed.scheme != 'https'
        or not valid_public_host(host)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or not 1 <= port <= 65535
    ):
        raise ProviderConfigurationError('provider_source_url_invalid')
    normalized_follow_hosts = frozenset(host.casefold() for host in follow_hosts)
    if len(normalized_follow_hosts) != len(follow_hosts):
        raise ProviderConfigurationError('provider_source_schema_invalid')
    return ProviderSource(
        provider_id=provider_id,
        adapter=adapter,
        kind=kind,
        url=url,
        host=host,
        follow_hosts=normalized_follow_hosts,
        user_agent=user_agent,
        subscription_user_agent=subscription_user_agent,
        enabled=enabled,
        authorization=authorization,
    )


def _load_document(path: Path) -> dict:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(descriptor, 'rb') as source_file:
            metadata = os.fstat(source_file.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_size > _MAX_SECRET_FILE_BYTES
            ):
                raise ProviderConfigurationError('provider_secret_file_invalid')
            raw = source_file.read(_MAX_SECRET_FILE_BYTES + 1)
        if len(raw) > _MAX_SECRET_FILE_BYTES:
            raise ProviderConfigurationError('provider_secret_file_invalid')
        document = json.loads(raw)
    except ProviderConfigurationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ProviderConfigurationError('provider_secret_file_invalid') from None
    if not isinstance(document, dict) or set(document) != {'providers'}:
        raise ProviderConfigurationError('provider_source_schema_invalid')
    return document


def load_provider_source(provider_id: str, adapter: str) -> ProviderSource:
    path_value = getattr(settings, 'PROVIDER_SOURCE_SECRET_FILE', '')
    if not isinstance(path_value, str) or not path_value or not os.path.isabs(path_value):
        raise ProviderConfigurationError('provider_secret_file_unconfigured')
    document = _load_document(Path(path_value))
    items = document['providers']
    if not isinstance(items, list) or not 1 <= len(items) <= _MAX_PROVIDERS:
        raise ProviderConfigurationError('provider_source_schema_invalid')
    sources = [_source_from_document(item) for item in items]
    ids = [source.provider_id for source in sources]
    if len(ids) != len(set(ids)):
        raise ProviderConfigurationError('provider_source_schema_invalid')
    matches = [source for source in sources if source.provider_id == provider_id]
    if len(matches) != 1 or matches[0].adapter != adapter:
        raise ProviderConfigurationError('provider_source_not_found')
    if not matches[0].enabled:
        raise ProviderConfigurationError('provider_source_disabled')
    return matches[0]
