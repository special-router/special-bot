"""Canonical non-secret origin metadata contract for SPECIAL Bot tooling."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

ROLLOUT_STATES = {'disabled', 'canary', 'pilot', 'production'}
ROLES = {'primary', 'secondary'}


def validate_origins(rows: list[dict[str, object]]) -> dict[str, object]:
    enabled = [row for row in rows if row.get('enabled') is True]
    ids = [str(row.get('id', '')) for row in rows]
    if not rows or any(not value for value in ids) or len(ids) != len(set(ids)):
        raise ValueError('origin ids must be non-empty and unique')
    primaries = [row for row in enabled if row.get('role') == 'primary']
    if len(primaries) != 1:
        raise ValueError('exactly one enabled primary origin is required')
    for row in rows:
        required = ('provider', 'region', 'public_host', 'transport')
        if any(not row.get(key) for key in required) or not isinstance(row.get('asn'), int):
            raise ValueError('provider, region, public_host, transport and integer ASN are required')
        if row.get('role') not in ROLES or row.get('rollout_state') not in ROLLOUT_STATES:
            raise ValueError('invalid role or rollout_state')
        if not isinstance(row.get('priority'), int) or int(row['priority']) < 0:
            raise ValueError('non-negative integer priority is required')
        url = urlsplit(str(row.get('health_url', '')))
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('health_url must be fixed HTTPS without userinfo/query/fragment')
        try:
            if ipaddress.ip_address(url.hostname).is_private:
                raise ValueError('health_url must not use a private address')
        except ValueError as error:
            if str(error) == 'health_url must not use a private address':
                raise
        if row.get('enabled') and row.get('rollout_state') == 'disabled':
            raise ValueError('enabled origin cannot have disabled rollout_state')
    distinct = {(str(row['provider']), int(row['asn'])) for row in enabled}
    return {
        'origins': len(rows),
        'enabled': len(enabled),
        'independent_origins_configured': len(enabled) >= 2 and len(distinct) >= 2,
    }
