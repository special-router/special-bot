from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlsplit

from apps.subscriptions.provider_snapshots import provider_snapshot_lines


_SAFE_TAG = re.compile(r'[^\w .()\-🇦-🇿]+', re.UNICODE)


def _tag(value: str, fallback: str) -> str:
    cleaned = _SAFE_TAG.sub('', unquote(value)).strip()[:64]
    return cleaned or fallback


def _vless(link: str, tag: str) -> dict | None:
    parts = urlsplit(link)
    query = {key: values[-1] for key, values in parse_qs(parts.query).items()}
    if parts.scheme != 'vless' or not parts.hostname or not parts.port or not parts.username:
        return None
    outbound = {
        'type': 'vless', 'tag': tag, 'server': parts.hostname,
        'server_port': parts.port, 'uuid': parts.username,
    }
    if query.get('flow'):
        outbound['flow'] = query['flow']
    network = query.get('type', 'tcp')
    if network == 'grpc':
        outbound['transport'] = {'type': 'grpc', 'service_name': query.get('serviceName', '')}
    elif network in ('xhttp', 'httpupgrade', 'ws'):
        outbound['transport'] = {'type': network, 'path': query.get('path', '/')}
        if query.get('host'):
            outbound['transport']['headers'] = {'Host': query['host']}
    security = query.get('security', '')
    if security in ('tls', 'reality'):
        tls = {
            'enabled': True,
            'server_name': query.get('sni') or query.get('serverName') or parts.hostname,
            'utls': {'enabled': True, 'fingerprint': query.get('fp', 'chrome')},
        }
        if security == 'reality':
            public_key = query.get('pbk') or query.get('publicKey')
            if not public_key:
                return None
            tls['reality'] = {'enabled': True, 'public_key': public_key}
            if query.get('sid'):
                tls['reality']['short_id'] = query['sid']
        outbound['tls'] = tls
    return outbound


def _hysteria2(link: str, tag: str) -> dict | None:
    parts = urlsplit(link)
    query = {key: values[-1] for key, values in parse_qs(parts.query).items()}
    if parts.scheme not in ('hysteria2', 'hy2') or not parts.hostname or not parts.port:
        return None
    password = parts.username or query.get('password')
    if not password:
        return None
    return {
        'type': 'hysteria2', 'tag': tag, 'server': parts.hostname,
        'server_port': parts.port, 'password': password,
        'tls': {'enabled': True, 'server_name': query.get('sni') or parts.hostname},
    }


def build_router_config() -> dict | None:
    """Build one bounded multi-country sing-box document from verified LKG data."""
    snapshots = provider_snapshot_lines()
    if not snapshots:
        return None
    endpoints = []
    used = set()
    countries: dict[str, list[str]] = {}
    for provider_id, lines in snapshots.items():
        for index, link in enumerate(lines):
            parts = urlsplit(link)
            label = _tag(parts.fragment, f'{provider_id}-{index + 1}')
            endpoint_tag = label
            suffix = 2
            while endpoint_tag in used:
                endpoint_tag = f'{label} {suffix}'
                suffix += 1
            outbound = _vless(link, endpoint_tag) or _hysteria2(link, endpoint_tag)
            if outbound is None:
                continue
            used.add(endpoint_tag)
            endpoints.append(outbound)
            country = label.split(' 2')[0].split(' 3')[0]
            countries.setdefault(country, []).append(endpoint_tag)
    if not endpoints:
        return None
    selectors = [
        {'type': 'selector', 'tag': country, 'outbounds': tags}
        for country, tags in countries.items() if len(tags) > 1
    ]
    return {
        'log': {'level': 'warn', 'timestamp': True},
        'outbounds': [
            {
                'type': 'urltest', 'tag': 'GLOBAL AUTO',
                'outbounds': [item['tag'] for item in endpoints],
                'url': 'https://cp.cloudflare.com/generate_204', 'interval': '5m',
            },
            *selectors,
            *endpoints,
        ],
        'route': {'final': 'GLOBAL AUTO', 'auto_detect_interface': True},
    }
