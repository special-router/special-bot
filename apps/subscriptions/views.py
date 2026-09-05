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
import copy
import datetime
import hashlib
import ipaddress
import json
import logging
import re
import socket
import ssl
import subprocess
import threading
import time
from functools import lru_cache
from urllib.parse import parse_qs, quote, unquote, unquote_to_bytes, urlencode, urlsplit

import httpx
from django.http import HttpResponse, HttpResponseNotFound
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from apps.servers.models import Server
from apps.servers.subscription_connector import build_subscription_url
from apps.subscriptions import page
from apps.subscriptions.devices import (
    bound_devices, client_hwid, client_metadata, device_limit_for, hwid_strict,
    register_device, valid_hwid)
from apps.subscriptions.models import MirrorEndpointLiveness
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


def _configured_params(inbound_id: int) -> dict | None:
    """Параметры Reality из настроек, без обращения к 3x-ui.

    После перехода на Remnawave старой панели, у которой их можно было
    спросить, больше нет: конфиг ноды задаём мы, и эти значения — то же самое,
    что в него положено. Совпадение с прежним inbound-ом и есть причина, по
    которой уже выданные ссылки продолжают работать.

    Порт здесь — тот, что слушает ядро (8443). Наружу клиент набирает 443 через
    nginx, и это отдельное значение (``SUBSCRIPTION_DIRECT_ADVERTISED_PORT``);
    подменять одно другим тут нельзя, иначе релейная линия получит чужой порт.
    """
    if not getattr(settings_relays(), 'REMNAWAVE_ENABLED', False):
        return None
    public_key = str(getattr(settings_relays(), 'REMNAWAVE_REALITY_PUBLIC_KEY', ''))
    server_name = str(getattr(settings_relays(), 'REMNAWAVE_REALITY_SERVER_NAME', ''))
    short_id = str(getattr(settings_relays(), 'REMNAWAVE_REALITY_SHORT_ID', ''))
    if not (public_key and server_name and short_id):
        # Неполные настройки дали бы ссылку, которая выглядит рабочей и не
        # подключается. Пусть лучше сработает прежний путь, пока он есть.
        return None
    return {
        'public_key': public_key,
        'server_name': server_name,
        'short_ids': [short_id],
        'port': int(getattr(settings_relays(), 'REMNAWAVE_REALITY_PORT', 8443) or 8443),
        'network': 'tcp',
        'inbound_id': inbound_id,
    }


def _get_params(server_id: int, inbound_id: int) -> dict:
    configured = _configured_params(inbound_id)
    if configured is not None:
        return configured
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


def _xhttp_link(uuid: str, host: str) -> str | None:
    """Линия XHTTP: тот же вход, что и у подписки, обычным HTTPS.

    Ничего не читает у панели: путь и порт — конфигурация, а не живое состояние
    inbound-а, потому что здесь нет ни Reality-параметров, ни short id, которые
    имело бы смысл сверять. Зато есть условие, без которого линия бесполезна и
    вредна: UUID клиента должен присутствовать в зеркальном inbound-е. За это
    отвечает ``MIRROR_INBOUND_IDS`` — тот же механизм, что наполняет остальные
    зеркала, — и включать выдачу до того, как зеркало настроено, нельзя.

    Ошибка в конфигурации гасит линию целиком: клиент, получивший строку в
    неработающий транспорт, читает её как «сервис сломан», и это хуже, чем на
    одну строку меньше.
    """
    config = _xhttp_config()
    if config is None:
        return None
    path, port = config
    query = urlencode(
        [
            ('type', 'xhttp'),
            ('security', 'tls'),
            ('sni', host),
            ('fp', 'chrome'),
            ('path', path),
            ('mode', 'auto'),
            ('host', host),
        ],
        quote_via=quote,
    )
    remark = f'{_endpoint_label(_OWN_REGION_CODE)} {_ALT_TRANSPORT_LABEL_SUFFIX}'
    return f'vless://{uuid}@{host}:{port}?{query}#{quote(remark)}'


def _xhttp_config() -> tuple[str, int] | None:
    """Validated (path, port) for the XHTTP transport, or None when unusable.

    Shared between the base64 line (``_xhttp_link``) and the Xray JSON
    outbound so the two branches can never disagree on what makes XHTTP safe
    to advertise.
    """
    from django.conf import settings
    if not getattr(settings, 'SUBSCRIPTION_XHTTP_ENABLED', False):
        return None
    path = getattr(settings, 'SUBSCRIPTION_XHTTP_PATH', '')
    if not isinstance(path, str) or not path.startswith('/') or len(path) > 128:
        return None
    if any(character in path for character in ' \r\n\t#?'):
        return None
    port = getattr(settings, 'SUBSCRIPTION_XHTTP_PORT', 443)
    if not isinstance(port, int) or not 1 <= port <= 65535:
        return None
    return path, port


def _grpc_link(uuid: str, host: str) -> str | None:
    """Линия gRPC: тот же узел, порт 80, Reality поверх gRPC.

    Существует потому, что транспорты душат по одному, а не все сразу: у
    партнёра TCP-Reality на нашем узле перестал подниматься, а gRPC на том же
    узле поднялся с первой попытки. Три разных транспорта на один и тот же
    выход — это три независимых шанса, и стоят они одну строку каждый.

    Reality-параметры берутся из конфигурации, а не из панели: у ``_internal_links``
    ровно обратный выбор, и он стоит логина плюс двух чтений всех inbound-ов на
    каждый запрос подписки — цена, приемлемая для одного канареечного клиента и
    неприемлемая для всех. Расходимость с панелью здесь не молчаливая: сменив
    ключ inbound-а, его меняют и тут, иначе линия перестаёт работать у всех
    сразу и это видно.
    """
    config = _grpc_config()
    if config is None:
        return None
    remark = f'{_endpoint_label(_OWN_REGION_CODE)} {_GRPC_LABEL_SUFFIX}'
    return _build_vless(
        uuid, host, config['port'], remark,
        {
            'public_key': config['public_key'],
            'server_name': config['server_name'],
            'short_ids': [config['short_id']],
            'network': 'grpc',
        },
        flow='',
        service_name=config['service_name'],
    )


def _grpc_config() -> dict | None:
    """Validated gRPC endpoint description, or None when it cannot be advertised.

    Shared between the base64 line and the Xray JSON outbound, exactly as
    ``_xhttp_config`` is, so the two branches cannot disagree about what makes
    the transport safe to hand out.

    A partially filled configuration is a mistake, not a degraded mode: every
    field below is required to dial Reality at all, and a link missing one of
    them fails at the client with no explanation.
    """
    from django.conf import settings
    if not getattr(settings, 'SUBSCRIPTION_GRPC_ENABLED', False):
        return None
    port = getattr(settings, 'SUBSCRIPTION_GRPC_PORT', 80)
    if not isinstance(port, int) or not 1 <= port <= 65535:
        return None
    fields = {}
    for key, name, limit in (
        ('service_name', 'SUBSCRIPTION_GRPC_SERVICE_NAME', 128),
        ('public_key', 'SUBSCRIPTION_GRPC_PUBLIC_KEY', 128),
        ('server_name', 'SUBSCRIPTION_GRPC_SERVER_NAME', 253),
        ('short_id', 'SUBSCRIPTION_GRPC_SHORT_ID', 16),
    ):
        value = getattr(settings, name, '')
        if not isinstance(value, str) or not value or len(value) > limit:
            return None
        if any(character in value for character in ' \r\n\t#?'):
            return None
        fields[key] = value
    # A short id is hex of even length; anything else is a typo that would be
    # silently accepted by the URL and rejected by the server.
    short_id = fields['short_id']
    if len(short_id) % 2 or any(character not in '0123456789abcdefABCDEF' for character in short_id):
        return None
    fields['port'] = port
    return fields


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


# v2rayNG's own UA is documented by its maintainers (`V2rayNG/<version>`).
# Happ has no official UA documentation; this substring match is a working
# hypothesis from live behaviour observed on a third-party provider (UA
# starting `Happ/<version>`), not a confirmed Happ spec.
_V2RAYNG_UA_PATTERN = re.compile(r'v2rayng', re.IGNORECASE)
_HAPP_UA_PATTERN = re.compile(r'happ', re.IGNORECASE)


def _wants_xray_json(user_agent: str, user_vpn_id: int | None = None) -> bool:
    """Whether this request should receive a raw Xray JSON body instead of base64.

    Три условия подряд, и порядок важен: флаг решает раньше UA, а список
    выкатки — раньше обоих.

    Список появился после аварии 2026-08-20. Ветка включалась одним флагом на
    всех сразу, и смена формата у клиента выглядит не как «формат другой», а как
    «серверы пропали»: вместо списка точек он видит один профиль. Формат — это
    то, что видит клиент целиком, поэтому менять его сразу для всех нельзя даже
    когда он проверен на стенде. Пустой список означает «никому», а не «всем»:
    ошибка в настройке должна оставлять людей на работающем формате.
    """
    from django.conf import settings
    if not getattr(settings, 'SUBSCRIPTION_XRAY_JSON_ENABLED', False):
        return False
    happ = bool(_HAPP_UA_PATTERN.search(user_agent))
    if not (happ or _V2RAYNG_UA_PATTERN.search(user_agent)):
        return False
    # Раскатка считается по клиенту, а не по всей ветке сразу. Документ,
    # проверенный на живом Happ, ничего не обещает про v2rayNG: он читает тот же
    # JSON другим кодом, и отказ у него выглядел бы так же — как пропавшие
    # серверы. Клиент попадает сюда после того, как на нём кто-то проверил
    # выдачу руками, а не после того, как ядро согласилось прочитать конфиг.
    rolled_out = getattr(settings, 'SUBSCRIPTION_XRAY_JSON_ROLLED_OUT_CLIENTS', [])
    if isinstance(rolled_out, list) and ('happ' in rolled_out if happ else 'v2rayng' in rolled_out):
        return True
    if getattr(settings, 'SUBSCRIPTION_XRAY_JSON_ALL_USERS_ENABLED', False):
        return True
    allowed = getattr(settings, 'SUBSCRIPTION_XRAY_JSON_TEST_USER_IDS', [])
    return isinstance(allowed, list) and user_vpn_id in allowed


def _reality_stream_settings(params: dict) -> dict:
    return {
        'network': params.get('network', 'tcp'),
        'security': 'reality',
        'realitySettings': {
            'serverName': params['server_name'],
            'fingerprint': 'chrome',
            'publicKey': params['public_key'],
            'shortId': params['short_ids'][0],
            'spiderX': '/',
        },
    }


def _xray_json_outbounds(uuid: str, params: dict, direct_host: str, direct_port: int,
                         relay_host: str, relay_port: int, flow: str) -> list[dict]:
    """Same three points as the base64 branch, tagged for the ``proxy`` balancer."""
    outbounds = [{
        'tag': 'proxy-nl-direct',
        'protocol': 'vless',
        'settings': {'vnext': [{
            'address': direct_host,
            'port': direct_port,
            'users': [{'id': uuid, 'encryption': 'none', 'flow': flow}],
        }]},
        'streamSettings': _reality_stream_settings(params),
    }]
    if relay_host:
        outbounds.append({
            'tag': 'proxy-ru-relay',
            'protocol': 'vless',
            'settings': {'vnext': [{
                'address': relay_host,
                'port': relay_port,
                'users': [{'id': uuid, 'encryption': 'none', 'flow': ''}],
            }]},
            'streamSettings': _reality_stream_settings(params),
        })
    xhttp_config = _xhttp_config()
    if xhttp_config is not None:
        path, port = xhttp_config
        outbounds.append({
            'tag': 'proxy-xhttp',
            'protocol': 'vless',
            'settings': {'vnext': [{
                'address': direct_host,
                'port': port,
                'users': [{'id': uuid, 'encryption': 'none', 'flow': ''}],
            }]},
            'streamSettings': {
                'network': 'xhttp',
                'security': 'tls',
                'tlsSettings': {'serverName': direct_host, 'fingerprint': 'chrome'},
                'xhttpSettings': {'path': path, 'host': direct_host, 'mode': 'auto'},
            },
        })
    grpc_config = _grpc_config()
    if grpc_config is not None:
        outbounds.append({
            'tag': 'proxy-grpc',
            'protocol': 'vless',
            'settings': {'vnext': [{
                'address': direct_host,
                'port': grpc_config['port'],
                'users': [{'id': uuid, 'encryption': 'none', 'flow': ''}],
            }]},
            'streamSettings': {
                'network': 'grpc',
                'security': 'reality',
                'realitySettings': {
                    'serverName': grpc_config['server_name'],
                    'fingerprint': 'chrome',
                    'publicKey': grpc_config['public_key'],
                    'shortId': grpc_config['short_id'],
                    'spiderX': '/',
                },
                'grpcSettings': {'serviceName': grpc_config['service_name']},
            },
        })
    outbounds.append({'tag': 'direct', 'protocol': 'freedom'})
    outbounds.append({'tag': 'block', 'protocol': 'blackhole'})
    return outbounds


def _xray_json_routing(balancers: list[dict], loop_rules: list[dict], entry: tuple[str, str]) -> dict:
    """Same rule order as ``darkcore-connections-service``'s routing.ts.

    Правила петель стоят раньше остальных: трафик приходит в них уже размеченным
    своим ``inboundTag``, и любое правило выше перехватило бы его по домену или
    адресу второй раз — то есть отправило бы в ``direct`` то, что первая ступень
    только что не смогла провести.

    DNS follows the complete profile shape that the same Happ client receives
    from the working provider: static bootstrap addresses, DoH resolvers,
    ``dns-out`` and a port-53 rule. The earlier experiment removed this while
    Happ was still overlaying its own routing rules; ``routing-enable: 0`` now
    removes that independent conflict, so a self-contained raw profile must not
    depend on client-global DNS state.

    ``entry`` говорит, чем начинается путь: балансировщиком или прямым
    outbound-ом. Второе — когда ступень всего одна: выбирать не из чего, а
    балансировщик, которому нечего выбрать, отдаёт трафик в никуда и показывает
    клиенту пинг «н/д». Лишнее звено здесь умеет только ломаться.
    """
    kind, tag = entry
    return {
        'domainStrategy': 'IPIfNonMatch',
        'domainMatcher': 'mph',
        'balancers': balancers,
        'rules': [
            *loop_rules,
            # DNS resolver tags become synthetic inbound tags inside Xray. The
            # working provider graph sends the Russian resolver direct and the
            # global resolvers through the first available VPN leg.
            {'type': 'field', 'inboundTag': ['yandex-dns'], 'outboundTag': 'direct'},
            {'type': 'field', 'inboundTag': ['google-dns', 'cloudflare-dns'], kind: tag},
            # A raw document disables Happ's global routing rules and therefore
            # carries its own DNS route. This rule precedes ordinary traffic:
            # name resolution is infrastructure for choosing a leg, not traffic
            # that should wait behind a dead first leg.
            {'type': 'field', 'network': 'tcp,udp', 'port': '53', 'outboundTag': 'dns-out'},
            # QUIC to 443 is blocked intentionally: browsers retry over TCP and
            # enter the same tunnel instead of leaving an unsupported UDP flow.
            {'type': 'field', 'network': 'udp', 'port': '443', 'outboundTag': 'block'},
            {'type': 'field', 'protocol': ['bittorrent'], 'outboundTag': 'direct'},
            {'type': 'field', 'ip': ['geoip:private'], 'outboundTag': 'direct'},
            {'type': 'field', 'domain': ['geosite:private'], 'outboundTag': 'direct'},
            {'type': 'field', 'domain': ['regexp:\\.ru$'], 'outboundTag': 'direct'},
            {'type': 'field', 'ip': ['geoip:ru'], 'outboundTag': 'direct'},
            {'type': 'field', 'network': 'tcp,udp', kind: tag},
        ],
    }


def _xray_json_inbounds() -> list[dict]:
    """Loopback-only adapters matching the working Happ profile shape."""
    return [
        {
            'tag': 'socks',
            'listen': '127.0.0.1',
            'port': 10808,
            'protocol': 'socks',
            'settings': {'auth': 'noauth', 'udp': True, 'userLevel': 8},
            'sniffing': {
                'enabled': True,
                'destOverride': ['http', 'tls', 'quic'],
                'routeOnly': True,
            },
        },
        {
            'tag': 'http',
            'listen': '127.0.0.1',
            'port': 10809,
            'protocol': 'http',
            'settings': {'userLevel': 8},
        },
    ]


def _xray_json_dns() -> dict:
    """Provider-validated DNS plumbing for a self-contained Happ profile."""
    return {
        'queryStrategy': 'UseIPv4',
        'hosts': {
            'cloudflare-dns.com': ['1.1.1.1', '1.0.0.1'],
            'one.one.one.one': ['1.1.1.1', '1.0.0.1'],
            'dns.google': ['8.8.8.8', '8.8.4.4'],
            'common.dot.dns.yandex.net': ['77.88.8.8', '77.88.8.1'],
        },
        'servers': [
            {'address': 'https://cloudflare-dns.com/dns-query',
             'tag': 'cloudflare-dns', 'timeoutMs': 1000},
            {'address': 'https://dns.google/dns-query',
             'tag': 'google-dns', 'timeoutMs': 1000},
            {'address': 'https://common.dot.dns.yandex.net/dns-query',
             'domains': ['geosite:category-ru'], 'tag': 'yandex-dns', 'timeoutMs': 1000},
            {'address': 'localhost', 'domains': ['geosite:private'], 'tag': 'localhost-dns'},
        ],
    }


_DNS_OUTBOUND = {
    'tag': 'dns-out',
    'protocol': 'dns',
    'settings': {'blockTypes': [28], 'nonIPQuery': 'skip'},
}


def _xray_json_burst_observatory() -> dict:
    """Failover latency is bounded by one full probe cycle, ``interval *
    sampling`` -- and Xray-core hard-floors ``interval`` at 10s, silently
    raising anything smaller.  10s is therefore the fastest cycle obtainable.

    ``sampling`` does not add probe traffic: probes/day per candidate is
    ``86400 / interval`` regardless of ``sampling``, because a higher sampling
    count only spreads the same per-cycle probe count over more, shorter
    sub-windows.  What it does cost is failover speed -- a candidate stays
    "Alive" until every cached sample slot has failed or expired, so
    ``sampling=3`` needs up to three cycles' worth of probes to evict all the
    stale-healthy slots left over from before it died.  ``sampling=1`` has
    exactly one slot, so the very next probe decides Alive/dead -- nothing
    left to evict.  The trade-off is noisier ranking (no averaging across
    samples), acceptable here because the candidates are our own two-to-three
    servers, not a large pool being fine-ranked by latency.

    ``timeout`` is left at 5s on purpose: our servers normally answer in well
    under a second, but a real client on a slow mobile network occasionally
    won't, and a shorter timeout would start declaring a merely-slow leg dead
    -- flapping is worse than a few extra seconds on the rare truly-dead case.
    """
    return {
        'subjectSelector': ['proxy'],
        'pingConfig': {
            'destination': 'https://www.google.com/generate_204',
            'interval': '10s',
            'timeout': '5s',
            'sampling': 1,
        },
    }


def _xray_json_ready(params: dict, direct_host: str) -> bool:
    """Whether every field the document needs is actually present and usable."""
    return bool(
        direct_host
        and isinstance(params.get('public_key'), str) and params.get('public_key')
        and isinstance(params.get('server_name'), str) and params.get('server_name')
        and isinstance(params.get('short_ids'), list) and params.get('short_ids')
        and isinstance(params['short_ids'][0], str) and params['short_ids'][0]
    )


def _cascade_strategy(members: list[str]) -> dict:
    """Стратегия ступени: порог задержки только там, где есть из чего выбирать.

    ``maxRTT`` отбрасывает кандидатов, чей замер хуже порога, — и кандидатов без
    замера вместе с ними. Ступень из одного outbound-а после такой фильтрации
    остаётся пустой, балансировщику некого выбрать, и клиент показывает пинг
    «н/д» при живом сервере. Так и вышло с зеркальными профилями: их вторая
    ступень — одна точка, замерить которую клиент не успевает или не умеет.

    Там, где кандидат один, выбирать не из чего по определению, поэтому порог не
    нужен: единственная задача балансировщика — отдать трафик этому outbound-у.
    """
    settings = {'expected': 1}
    if len(members) > 1:
        settings['maxRTT'] = '1500ms'
    return {'type': 'leastLoad', 'settings': settings}


def _cascade(stages: list[tuple[str, list[str]]], name: str) -> tuple[list[dict], list[dict], list[dict], str]:
    """Собрать лестницу «не вышло — пробуй следующим» из списка ступеней.

    Одним балансировщиком это не выражается: он выбирает быстрейший из живых, а
    здесь нужен именно порядок — сначала обычный TCP, и только когда он молчит,
    остальное. Xray-core умеет это единственным способом, который и использует
    провайдер: у балансировщика есть ``fallbackTag``, а ``loopback``-outbound
    возвращает трафик в собственный роутинг под другим тегом, где его ждёт
    следующий балансировщик.

    Каждая ступень — пара «тег балансировщика, список тегов outbound-ов». Внутри
    ступени балансировщик выбирает по замерам, между ступенями порядок жёсткий.
    Последняя ступень падать некуда, поэтому ``fallbackTag`` ей не даётся: с ним
    она указывала бы на несуществующий тег, и Xray отказался бы читать документ
    целиком.

    Возвращает outbound-ы петель, балансировщики, правила петель и тег
    балансировщика первой ступени — то, чем правило «весь остальной трафик»
    начинает лестницу.
    """
    # Одна ступень с одним outbound-ом — это не лестница, а обычный маршрут.
    # Балансировщик над единственным кандидатом ничего не решает, но добавляет
    # условие, при котором трафик может никуда не пойти: пока observatory не
    # принёс замер, выбирать ему не из чего. Клиент показывает это как «н/д».
    if len(stages) == 1 and len(stages[0][1]) == 1:
        return [], [], [], ('outboundTag', stages[0][1][0])

    loops, balancers, rules = [], [], []
    for index, (tag, members) in enumerate(stages):
        balancer = {'tag': tag, 'selector': members, 'strategy': _cascade_strategy(members)}
        if index + 1 < len(stages):
            reroute = f'{name}-L{index + 2}-REROUTE'
            loop_tag = f'LOOP-{name}-L{index + 2}'
            balancer['fallbackTag'] = loop_tag
            loops.append({
                'tag': loop_tag,
                'protocol': 'loopback',
                'settings': {'inboundTag': reroute},
            })
            rules.append({
                'type': 'field',
                'inboundTag': [reroute],
                'balancerTag': stages[index + 1][0],
            })
        balancers.append(balancer)
    return loops, balancers, rules, ('balancerTag', stages[0][0])


def _build_xray_json(uuid: str, params: dict, direct_host: str, direct_port: int,
                     relay_host: str, relay_port: int, flow: str,
                     own_outbounds: list[dict] | None = None) -> dict:
    """Наш профиль для Happ/v2rayNG: три транспорта одного узла лестницей.

    Порядок ступеней здесь — не результат замера, а решение: обычный TCP, затем
    XHTTP, затем gRPC. Плоский ``leastPing`` выбирал бы быстрейший, а быстрейшим
    почти всегда оказывается TCP — до того момента, когда оператор его задушил,
    и тогда клиенту нужен не самый быстрый, а любой работающий. Лестница даёт
    ровно это: следующая ступень включается, когда предыдущая перестала
    отвечать.

    Профиль самодостаточен: он несёт loopback-only SOCKS/HTTP adapters, DNS и
    маршрутизацию, потому что ответ с ``routing-enable: 0`` отключает глобальные
    правила Happ и не должен зависеть от клиентской конфигурации.

    ``own_outbounds`` подменяет только сами точки: лестница, балансировщики и
    маршрутизация остаются здешними, потому что теги те же. Так профиль и
    список берут одни и те же четыре точки из панели, не повторяя её логику
    сборки ссылок.
    """
    outbounds = own_outbounds if own_outbounds else _xray_json_outbounds(
        uuid, params, direct_host, direct_port, relay_host, relay_port, flow)
    tags = {outbound['tag'] for outbound in outbounds}
    stages = [('own-l1', [tag for tag in ('proxy-nl-direct', 'proxy-ru-relay') if tag in tags])]
    for tag, balancer in (('proxy-xhttp', 'own-l2'), ('proxy-grpc', 'own-l3')):
        if tag in tags:
            stages.append((balancer, [tag]))
    loops, balancers, loop_rules, entry = _cascade(stages, 'OWN')
    document = {
        'remarks': f'{_endpoint_label(_OWN_REGION_CODE)} авто',
        'log': {'loglevel': 'warning'},
        'inbounds': _xray_json_inbounds(),
        'dns': _xray_json_dns(),
        'policy': {
            'levels': {'0': {
                'handshake': 4, 'connIdle': 300, 'uplinkOnly': 2,
                'downlinkOnly': 5, 'bufferSize': 10240,
            }},
        },
        'routing': _xray_json_routing(balancers, loop_rules, entry),
        'outbounds': outbounds + loops + [_DNS_OUTBOUND],
    }
    # Замеры существуют ради выбора. Без балансировщиков выбирать нечего, и
    # секция осталась бы работой, результат которой никто не читает.
    if balancers:
        document['burstObservatory'] = _xray_json_burst_observatory()
    return document


def _xray_outbound_from_link(link: str, tag: str) -> dict | None:
    """Перевести одну готовую строку подписки в outbound того же смысла.

    Источником служат уже отобранные строки, а не документ провайдера заново:
    так у профиля и у списка ровно один набор точек, прошедший одни и те же
    ограничения — лимиты, дедупликацию и вердикты живости. Разойтись им негде.
    """
    try:
        parts = urlsplit(link)
        query = {key: values[0] for key, values in parse_qs(parts.query).items()}
        host, port, secret = parts.hostname, parts.port, parts.username
    except ValueError:
        return None
    if not host or not port or not secret:
        return None
    if parts.scheme == 'hy2':
        server_name = query.get('sni', '')
        if not server_name:
            return None
        # Xray-core сам hysteria не умеет; это форк ядра, на котором работает
        # Happ, и такую же секцию отдаёт провайдер. Вызывающий решает, кому
        # такой outbound можно показывать.
        return {
            'tag': tag,
            'protocol': 'hysteria',
            'settings': {'address': host, 'port': port, 'version': 2},
            'streamSettings': {
                'network': 'hysteria',
                'security': 'tls',
                # ``finalmask`` описывает, как форк поднимает QUIC. Провайдер
                # шлёт эту секцию в каждом своём hysteria-outbound; повторяем
                # его значения, а не подбираем свои: чем ближе к документу,
                # который у того же клиента уже работает, тем меньше остаётся
                # мест, где может разойтись поведение.
                'finalmask': {'quicParams': {
                    'bbrProfile': 'standard',
                    'congestion': 'bbr',
                    'maxIdleTimeout': 4,
                }},
                'hysteriaSettings': {'auth': secret, 'version': 2},
                'tlsSettings': {
                    'serverName': server_name,
                    'alpn': ['h3'],
                    'fingerprint': query.get('fp', 'chrome'),
                },
            },
        }
    if parts.scheme != 'vless':
        return None
    network = query.get('type', 'tcp')
    security = query.get('security', 'none')
    stream = {'network': network, 'security': security}
    if security == 'reality':
        reality = {
            'serverName': query.get('sni', ''),
            'fingerprint': query.get('fp', 'chrome'),
            'publicKey': query.get('pbk', ''),
            'spiderX': query.get('spx', '/'),
        }
        if query.get('sid'):
            reality['shortId'] = query['sid']
        if not reality['serverName'] or not reality['publicKey']:
            return None
        stream['realitySettings'] = reality
    elif security == 'tls':
        stream['tlsSettings'] = {
            'serverName': query.get('sni', host),
            'fingerprint': query.get('fp', 'chrome'),
        }
    if network == 'grpc':
        stream['grpcSettings'] = {'serviceName': query.get('serviceName', '')}
    elif network == 'ws':
        stream['wsSettings'] = {'path': query.get('path', '/')}
    elif network == 'xhttp':
        stream['xhttpSettings'] = {
            'path': query.get('path', '/'),
            'host': query.get('host', host),
            'mode': query.get('mode', 'auto'),
        }
    user = {'id': secret, 'encryption': 'none', 'flow': query.get('flow', '')}
    outbound = {
        'tag': tag,
        'protocol': 'vless',
        'settings': {'vnext': [{'address': host, 'port': port, 'users': [user]}]},
        'streamSettings': stream,
    }
    # Vision несовместим с мультиплексированием: соединение с ``flow`` несёт
    # собственное обрамление, поверх которого mux ломает поток. XHTTP
    # мультиплексирует сам, на уровне транспорта, и Mux.Cool поверх него рвёт
    # соединение сразу после установки: клиент получает EOF на первом же
    # запросе, а строка при этом выглядит совершенно исправной.
    if not user['flow'] and network != 'xhttp':
        outbound['mux'] = {
            'enabled': True,
            'concurrency': 6,
            'xudpConcurrency': 4,
            'xudpProxyUDP443': 'reject',
        }
    return outbound


def _mirror_xray_profiles(links: list[str], allow_hysteria: bool) -> list[dict]:
    """Профиль на страну: её точки лестницей в том порядке, в каком они выданы.

    Порядок строк в списке — это и есть порядок попыток: прямая точка страны
    идёт первой, её запасной транспорт следом. Здесь он только переносится в
    ``fallbackTag``, чтобы перебирал клиент, а не человек.

    Страна, у которой осталась одна точка, профилем тоже становится: лестница
    из одной ступени — это обычный outbound, и отказывать ей значило бы терять
    страну целиком там, где список её показывает.
    """
    grouped: dict[str, list[str]] = {}
    for link in links:
        label = unquote(link.partition('#')[2])
        country = label.removesuffix(_ALT_TRANSPORT_LABEL_SUFFIX).strip()
        if not country:
            continue
        grouped.setdefault(country, []).append(link)
    profiles = []
    for index, (country, country_links) in enumerate(grouped.items()):
        prefix = f'M{index}'
        outbounds, stages = [], []
        for position, link in enumerate(country_links):
            tag = f'{prefix}-s{position}'
            outbound = _xray_outbound_from_link(link, tag)
            if outbound is None:
                continue
            if outbound['protocol'] == 'hysteria' and not allow_hysteria:
                continue
            outbounds.append(outbound)
            stages.append((f'{prefix}-b{position}', [tag]))
        if not outbounds:
            continue
        loops, balancers, loop_rules, entry = _cascade(stages, prefix)
        profile = {
            'remarks': country,
            'log': {'loglevel': 'warning'},
            'inbounds': _xray_json_inbounds(),
            'dns': _xray_json_dns(),
            'routing': _xray_json_routing(balancers, loop_rules, entry),
            'outbounds': outbounds + loops + [
                {'tag': 'direct', 'protocol': 'freedom'},
                {'tag': 'block', 'protocol': 'blackhole'},
                _DNS_OUTBOUND,
            ],
        }
        if balancers:
            # Настройки замера скопированы с провайдера, чей документ у того же
            # клиента работает: адрес Cloudflare вместо Google, который у части
            # операторов не отвечает и без всякого туннеля, явный GET и вдвое
            # больший таймаут. Чужая точка за границей отвечает медленнее нашей,
            # и пять секунд для неё — повод объявить мёртвым то, что живо.
            profile['burstObservatory'] = {
                'subjectSelector': [prefix],
                'pingConfig': {
                    'connectivity': 'https://one.one.one.one/',
                    'destination': 'https://one.one.one.one/media/content-filter.png',
                    'httpMethod': 'GET',
                    'interval': '10s',
                    'timeout': '10s',
                    'sampling': 1,
                },
            }
        profiles.append(profile)
    return profiles


# Xray-core сам hysteria2 не поддерживает: секция ``hysteriaSettings`` живёт
# только в форке ядра, на котором работает Happ. v2rayNG на таком outbound-е
# отказывается читать документ целиком, поэтому ступень с ним показывается
# ровно тому клиенту, который её понимает.
_HYSTERIA_CAPABLE_UA_PATTERN = re.compile(r'happ', re.IGNORECASE)


def _wants_hysteria_outbound(user_agent: str) -> bool:
    return bool(_HYSTERIA_CAPABLE_UA_PATTERN.search(user_agent))


# Заголовки, которые Remnawave выставляет сам и которые обязаны остаться
# нашими: расчётный период считает бот по балансу, а не панель по своему
# ``expireAt``. Два источника срока в одном ответе — это два разных числа на
# экране у одного человека.
_REMNAWAVE_OVERRIDDEN_HEADERS = (
    'subscription-userinfo', 'profile-title', 'announce', 'profile-web-page-url')
# Заголовки, по которым панель выбирает шаблон конфигурации и считает
# устройства. Без них клиент получает документ не для своего приложения.
_REMNAWAVE_FORWARDED_HEADERS = (
    ('HTTP_USER_AGENT', 'User-Agent'),
    ('HTTP_ACCEPT', 'Accept'),
    ('HTTP_X_HWID', 'x-hwid'),
    ('HTTP_X_DEVICE_OS', 'x-device-os'),
    ('HTTP_X_VER_OS', 'x-ver-os'),
    ('HTTP_X_DEVICE_MODEL', 'x-device-model'),
)
_REMNAWAVE_TIMEOUT = 10.0
_REMNAWAVE_MAX_BYTES = 1024 * 1024
# Наши собственные точки, взятые из панели вместо настроек. Панель отдаёт их
# под тем же ``sub_id`` без токена и уже с UUID этого клиента, поэтому здесь
# нет ни ключей, ни сборки ссылок — только перенос готовых строк. Девять
# зеркальных стран панель не знает и знать не может: у зеркала одна учётка на
# всех, а всякая ссылка панели несёт личный UUID. Поэтому берётся ровно наша
# часть, а остальное по-прежнему добавляем мы.
_PANEL_LINKS_TIMEOUT = 5.0
_PANEL_LINKS_MAX_BYTES = 64 * 1024
_PANEL_LINKS_USER_AGENT = 'v2rayN/6.0'


def _panel_links_allowed(user_vpn_id: int) -> bool:
    """Кому выдача собирается из панели, а кому по-прежнему из настроек.

    Три ступени, как у запасных точек, и по той же причине: полная раскатка —
    отдельное состояние, а не список, в который каждый раз надо дописывать
    нового клиента. День, когда его не дописали, выглядит как молча укоротившаяся
    подписка у одного человека.
    """
    relays = settings_relays()
    if not getattr(relays, 'REMNAWAVE_ENDPOINTS_ENABLED', False):
        return False
    if not str(getattr(relays, 'REMNAWAVE_API_URL', '')).strip():
        return False
    if getattr(relays, 'REMNAWAVE_ENDPOINTS_ALL_USERS_ENABLED', False):
        return True
    allowed = getattr(relays, 'REMNAWAVE_ENDPOINTS_TEST_USER_IDS', [])
    # Пустой или битый список во время раскатки = не получает никто.
    return isinstance(allowed, list) and bool(allowed) and user_vpn_id in allowed


def _panel_links(user_vpn) -> list[str] | None:
    """Наши точки в том виде, в каком их отдаёт панель, или ``None``.

    ``None`` означает «панель не ответила» и возвращает выдачу на сборку из
    настроек — то есть на сегодняшнее поведение. Пустой список сюда не
    приравнивается: панель без хостов ответила бы «серверов нет», а это ровно
    та авария, ради которой прокси-путь и был отключён.

    Чужие строки отбрасываются по UUID. Панель обязана вернуть точки этого
    клиента, и если в ответе оказалась чужая идентичность, это не деградация,
    которую можно пережить, а выдача чужого доступа.
    """
    if not _panel_links_allowed(user_vpn.id):
        return None
    base = str(getattr(settings_relays(), 'REMNAWAVE_API_URL', '')).strip().rstrip('/')
    try:
        response = httpx.get(f'{base}/api/sub/{user_vpn.sub_id}',
                             headers={'User-Agent': _PANEL_LINKS_USER_AGENT},
                             timeout=_PANEL_LINKS_TIMEOUT, follow_redirects=False)
    except httpx.HTTPError as error:
        # Ни адреса, ни ``sub_id`` в логе: ссылка подписки — доступ к трафику.
        logger.warning('Panel endpoints unavailable: %s', type(error).__name__)
        return None
    if response.status_code != 200 or len(response.content) > _PANEL_LINKS_MAX_BYTES:
        logger.warning('Panel endpoints refused: status=%s', response.status_code)
        return None
    try:
        decoded = _decode_subscription_payload(response.content)
    except ValueError:
        return None
    expected = str(user_vpn.vpn_uuid)
    links = []
    for raw_line in decoded.splitlines():
        if not raw_line.startswith(b'vless://'):
            continue
        line = raw_line.decode('utf-8', 'replace')
        try:
            if urlsplit(line).username != expected:
                continue
        except ValueError:
            continue
        links.append(line)
    return links or None


def _panel_outbound_tag(link: str, direct_host: str) -> str:
    """Ступень лестницы, которой соответствует строка панели.

    Теги те же, что строит ``_xray_json_outbounds``, потому что маршрутизацию,
    балансировщики и порядок ступеней документ берёт из них. Новый тег здесь
    означал бы точку, на которую не ссылается ни одно правило: клиент её видит
    и не использует.
    """
    try:
        parts = urlsplit(link)
        query = {key: values[0] for key, values in parse_qs(parts.query).items()}
    except ValueError:
        return ''
    network = query.get('type', 'tcp')
    if network == 'xhttp':
        return 'proxy-xhttp'
    if network == 'grpc':
        return 'proxy-grpc'
    if network != 'tcp':
        return ''
    return 'proxy-nl-direct' if parts.hostname == direct_host else 'proxy-ru-relay'


def _panel_outbounds(links: list[str], direct_host: str) -> list[dict]:
    """Те же строки, что уходят списком, но секциями документа.

    Один источник на оба формата: список и профиль расходились бы иначе на
    любой правке хостов в панели, а расхождение здесь видно только у клиента.
    """
    outbounds = []
    taken = set()
    for link in links:
        tag = _panel_outbound_tag(link, direct_host)
        if not tag or tag in taken:
            continue
        outbound = _xray_outbound_from_link(link, tag)
        if outbound is None:
            continue
        taken.add(tag)
        outbounds.append(outbound)
    return outbounds


def _remnawave_proxy_enabled() -> bool:
    return bool(getattr(settings_relays(), 'REMNAWAVE_SUBSCRIPTION_PROXY_ENABLED', False)
                and str(getattr(settings_relays(), 'REMNAWAVE_SUBSCRIPTION_BASE_URL', '')).strip())


def _remnawave_upstream(request, user_vpn) -> tuple[bytes, str, dict[str, str]] | None:
    """Забрать документ подписки из Remnawave под тем же ``sub_id``.

    Апстрим свой, а не чужой, поэтому здесь нет проверок на приватные адреса из
    ``_cached_upstream_links``: их задача — не дать чужой ссылке увести запрос
    внутрь сети, а этот адрес задаём мы. Ответ всё равно ограничен по размеру:
    ошибка в конфигурации панели не должна расти в каждом ответе клиенту.
    """
    base = str(getattr(settings_relays(), 'REMNAWAVE_SUBSCRIPTION_BASE_URL', '')).rstrip('/')
    headers = {}
    for meta_key, header_name in _REMNAWAVE_FORWARDED_HEADERS:
        value = request.META.get(meta_key)
        if value:
            headers[header_name] = value
    try:
        response = httpx.get(f'{base}/{user_vpn.sub_id}', headers=headers,
                             timeout=_REMNAWAVE_TIMEOUT, follow_redirects=False)
    except httpx.HTTPError as error:
        # Ни адрес, ни sub_id в лог не идут: ссылка подписки — это доступ к
        # трафику конкретного человека.
        logger.warning('Remnawave upstream failed: %s', type(error).__name__)
        return None
    if response.status_code != 200 or len(response.content) > _REMNAWAVE_MAX_BYTES:
        logger.warning('Remnawave upstream refused: status=%s', response.status_code)
        return None
    passthrough = {
        name: value for name, value in response.headers.items()
        if name.lower() not in _REMNAWAVE_OVERRIDDEN_HEADERS
        and name.lower() not in ('content-length', 'content-encoding', 'transfer-encoding')
    }
    return response.content, response.headers.get('content-type', 'text/plain'), passthrough


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

    # Balance / remaining days for the status remark and the expiry header.
    # Negative balances exist (a manual debit can outrun the balance), and they
    # must not become a term: no entitlement is zero days, never minus two.
    user = TelegramUser.objects.annotate_balance().filter(id=user_vpn.user_id).first()
    price = float(server.tariff.price) if server.tariff else 0.0
    balance = float(getattr(user, 'balance', 0) or 0) if user else 0.0
    days = max(int(balance // price), 0) if price > 0 else 0
    status_label = f'осталось {days} дней' if days > 0 else 'подписка окончена'

    # Remnawave отдаёт документ, собранный под конкретное приложение, поэтому
    # весь разбор User-Agent ниже его не касается. Стоит до чтения параметров из
    # 3x-ui: в этом режиме к старой панели не должно уходить ни одного запроса.
    if _remnawave_proxy_enabled():
        upstream = _remnawave_upstream(request, user_vpn)
        if upstream is not None:
            body, content_type, passthrough = upstream
            resp = HttpResponse(body, content_type=content_type)
            _with_headers(resp, passthrough)
            # Наши заголовки последними: срок считает бот, и он же владеет
            # названием профиля и объявлением.
            resp['Profile-Update-Interval'] = '12'
            _with_headers(resp, _client_ui_headers(days))
            return _no_cache_response(_with_headers(resp, hwid_headers))
        # Панель недоступна — отдаём то, что умеет старый путь, пока он жив.
        # Пустой ответ или 502 клиент читает как «серверов нет», а это ровно та
        # авария, которую мы уже видели 2026-08-20.

    params = _get_params(server.id, server.inbound_id)

    # Fetch panel-managed own endpoints once: list and JSON output must use the
    # same snapshot, especially while hosts are being changed in the panel.
    panel_links = _panel_links(user_vpn)

    # Client endpoint hosts.
    # Direct = public NL sub domain on the inbound port.
    # Relay  = the client_vpn_host stored on the server (e.g. the RU relay front).
    relay_host, relay_port = _endpoint(server.client_vpn_host, params['port'])
    # Config delivery and the VPN data plane deliberately use different hosts.
    # ``SUBSCRIPTION_BASE_URL`` is CDN-fronted and cannot terminate Reality,
    # XHTTP or gRPC. The VPN host comes from the server/control-plane record.
    sub_domain = settings_relays().SUBSCRIPTION_BASE_URL.split('/')[2].split(':')[0]
    direct_host = str(getattr(server, 'vpn_url', '') or '').strip()
    try:
        parsed_vpn_url = urlsplit(direct_host if '://' in direct_host else f'https://{direct_host}')
        direct_host = parsed_vpn_url.hostname or ''
    except ValueError:
        direct_host = ''
    if not direct_host:
        direct_host = str(getattr(server, 'ip_address', '') or '').strip()
    if not direct_host:
        direct_host = sub_domain
    # Panel-managed endpoint links are authoritative for the VPN data plane.
    # Config delivery now lives on a separate CDN hostname, so using it to
    # classify the panel's direct endpoint would mis-tag every own TCP link.
    # Derive the direct VPN host from the first non-relay panel TCP line.
    if panel_links:
        for panel_link in panel_links:
            try:
                parts = urlsplit(panel_link)
                query = {key: values[0] for key, values in parse_qs(parts.query).items()}
            except ValueError:
                continue
            if query.get('type', 'tcp') == 'tcp' and parts.hostname and parts.hostname != relay_host:
                direct_host = parts.hostname
                break
    # Advertise the shared public listener when configured; xray may then bind
    # its inbound privately without changing what any client dials.
    direct_port = getattr(settings_relays(), 'SUBSCRIPTION_DIRECT_ADVERTISED_PORT', 0) or params['port']

    uuid_str = str(user_vpn.vpn_uuid)
    # Preserve the deployed legacy client contract. Most existing control-plane
    # clients have no Vision flow, and forcing it in the generated subscription
    # makes those links intermittently land on an incompatible same-port
    # listener. Vision may be promoted only by an explicit per-client migration.
    flow = ''

    user_agent = request.META.get('HTTP_USER_AGENT', '')
    if _wants_xray_json(user_agent, user_vpn.id) and (
            panel_links or _xray_json_ready(params, direct_host)):
        try:
            # Массив, а не один объект: клиент рисует по профилю на элемент и
            # берёт имя из ``remarks``. Наш узел идёт первым — он единственный,
            # за который отвечаем мы, а всё после него чужое.
            documents = [_build_xray_json(
                uuid_str, params, direct_host, direct_port, relay_host, relay_port, flow,
                own_outbounds=_panel_outbounds(panel_links, direct_host) if panel_links else None)]
            if _is_backup_test_user(user_vpn.id):
                native_profiles = _native_mirror_profiles() \
                    if _native_mirror_profiles_enabled(user_agent) else None
                if native_profiles:
                    documents.extend(native_profiles)
                else:
                    # Native failure costs only fidelity, never availability:
                    # retain the already-shipped bounded endpoint profiles.
                    documents.extend(_mirror_xray_profiles(
                        _backup_links() or [], _wants_hysteria_outbound(user_agent)))
            body = json.dumps(documents).encode('utf-8')
        except (KeyError, TypeError, ValueError):
            body = None
        if body is not None:
            resp = HttpResponse(body, content_type='application/json')
            resp['Profile-Update-Interval'] = '12'
            # Клиент держит собственный набор правил маршрутизации и по
            # умолчанию накладывает его поверх профиля. Для списка ссылок это
            # ровно то, что нужно: своих правил там нет. У документа они свои, и
            # два набора одновременно дают профиль, который соединяется, но
            # никуда не ведёт — трафик уходит по чужим правилам. Провайдер, чей
            # документ на этом клиенте работает, гасит их тем же заголовком.
            resp['routing-enable'] = '0'
            _with_headers(resp, _client_ui_headers(days))
            return _no_cache_response(_with_headers(resp, hwid_headers))

    links = []
    # 1) Status entry (non-working) first, matching the happ UX.  It exists only
    # to show the remaining term to a client that reads no headers; the same
    # number now also ships in ``subscription-userinfo``, so this is retirable
    # once a real client is seen rendering that header.
    if getattr(settings_relays(), 'SUBSCRIPTION_STATUS_ENTRY_ENABLED', True):
        links.append(_build_vless(uuid_str, '127.0.0.1', 1, f'📊 Подписка-{status_label}', params, flow=''))
    # 2) and 3) are the two endpoints this deployment operates and is
    # accountable for, so a customer scanning the list reaches them before
    # anything a third party serves.  Everything below them is a fallback.
    # Панель, когда отвечает, и есть их описание: те же четыре точки, но
    # заведённые там, где их правят. Настройки остаются запасным путём, а не
    # вторым источником — иначе правка хостов расходилась бы с выдачей молча.
    if panel_links:
        links.extend(panel_links)
    else:
        # 2) Direct NL primary.
        links.append(_build_vless(uuid_str, direct_host, direct_port,
                                  _endpoint_label(_OWN_REGION_CODE), params, flow=flow))
        # 3) RU relay (only if configured).
        if relay_host:
            links.append(_build_vless(uuid_str, relay_host, relay_port,
                                      _endpoint_label(_OWN_REGION_CODE, whitelisted=True),
                                      params, flow=flow))
        # 3.5) XHTTP на том же имени и порту, что и сама подписка. Стоит сразу
        # за нашими двумя линиями: это наш endpoint, и он единственный не
        # опознаётся как VPN-хендшейк — то есть первое, что стоит попробовать,
        # когда обычная линия перестала работать у конкретного оператора.
        xhttp_link = _xhttp_link(uuid_str, direct_host)
        if xhttp_link:
            links.append(xhttp_link)
        # 3.6) gRPC на том же узле, порт 80. Идёт после XHTTP, потому что XHTTP
        # неотличим от обычного HTTPS, а этот — отличим; но он держится там, где
        # уже не поднимается TCP-Reality, и это проверено на живой сети партнёра.
        grpc_link = _grpc_link(uuid_str, direct_host)
        if grpc_link:
            links.append(grpc_link)
    # 4) Same-origin internal transport canary. Every candidate independently
    # stable-reads its own live inbound and silently omits on any uncertainty.
    if _is_internal_test_user(user_vpn.id):
        links.extend(_internal_links(server.id, uuid_str))
    # 5) External backup endpoints (feature-gated test group).
    backup_links = _backup_links() if _is_backup_test_user(user_vpn.id) else None
    if backup_links:
        links.extend(backup_links)

    # Браузеру — страница, приложению — документ. Стоит после сборки links,
    # поэтому страница показывает ровно тот список, который получит клиент:
    # разойтись им нечем.
    if page.wants_page(request):
        html = page.render(
            subscription_url=build_subscription_url(settings_relays().SUBSCRIPTION_BASE_URL, sub_id),
            days=days,
            status_label=status_label,
            links=links,
            devices=bound_devices(user_vpn),
            device_limit=device_limit_for(user_vpn),
        )
        resp = HttpResponse(html, content_type='text/html; charset=utf-8')
        return _no_cache_response(_with_headers(resp, hwid_headers))

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
    """Whether this subscription receives third-party endpoints.

    Full rollout is its own state rather than an allowlist holding every id
    there happens to be today.  A list has to be rewritten for each new
    customer, and the day it is not, that customer silently gets a smaller
    subscription than the one beside them — a rollout nobody notices is
    incomplete is worse than one that never started.
    """
    from django.conf import settings
    if not getattr(settings, 'SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED', False):
        return False
    if getattr(settings, 'SUBSCRIPTION_BACKUP_ALL_USERS_ENABLED', False):
        return True
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
# Complete Happ configs are cached separately from rendered URI lines: the two
# fetch the same bearer source under different format-selecting User-Agents and
# must never satisfy one another's cache lookup.
_NATIVE_PROFILE_CACHE: dict[str, tuple[float, list[dict]]] = {}
_NATIVE_PROFILE_FETCHING: dict[str, threading.Event] = {}


def _backup_links() -> list[str] | None:
    """Return a bounded, stable-deduplicated aggregate of opaque proxy lines."""
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
    # Two caps with two jobs: the aggregate limit protects the response size, and
    # this one protects the list a customer scrolls, so our own status, Direct
    # and Relay lines stay reachable whatever a provider decides to return. The
    # tighter of the two decides.
    line_limit = min(line_limit, int(_bounded_number(
        getattr(settings, 'SUBSCRIPTION_BACKUP_MAX_MIRROR_ENTRIES', 16),
        default=16, lower=1, upper=2048)))
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
        _NATIVE_PROFILE_CACHE.clear()
        _BACKUP_CACHE_GENERATION += 1


def _evict_backup_cache(active_keys: set[str]) -> None:
    now = time.monotonic()
    with _BACKUP_CACHE_LOCK:
        for key, (expiry, _links) in list(_BACKUP_CACHE.items()):
            if expiry <= now or key not in active_keys:
                _BACKUP_CACHE.pop(key, None)
        while len(_BACKUP_CACHE) > _BACKUP_CACHE_HARD_MAX_ENTRIES:
            _BACKUP_CACHE.pop(next(iter(_BACKUP_CACHE)), None)
        for key, (expiry, _profiles) in list(_NATIVE_PROFILE_CACHE.items()):
            # Native keys include the fixed format selector after a NUL byte.
            source_key = key.partition('\x00')[0]
            if expiry <= now or source_key not in active_keys:
                _NATIVE_PROFILE_CACHE.pop(key, None)
        while len(_NATIVE_PROFILE_CACHE) > _BACKUP_CACHE_HARD_MAX_ENTRIES:
            _NATIVE_PROFILE_CACHE.pop(next(iter(_NATIVE_PROFILE_CACHE)), None)


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


_NATIVE_PROFILE_MAX_PROFILES = 16
_NATIVE_PROFILE_MAX_OUTBOUNDS = 128
_NATIVE_PROFILE_MAX_BALANCERS = 32
_NATIVE_PROFILE_MAX_RULES = 128
_NATIVE_PROFILE_MAX_DEPTH = 24
_NATIVE_PROFILE_MAX_STRING = 8192
_NATIVE_PROFILE_ALLOWED_KEYS = frozenset({
    'remarks', 'log', 'dns', 'inbounds', 'outbounds', 'routing', 'policy',
    'stats', 'burstObservatory',
})
_NATIVE_PROFILE_ALLOWED_PROTOCOLS = frozenset({
    'vless', 'hysteria', 'freedom', 'blackhole', 'dns', 'loopback',
})
_NATIVE_PROFILE_ALLOWED_INBOUND_PROTOCOLS = frozenset({'socks', 'http'})


def _native_mirror_profiles_enabled(user_agent: str) -> bool:
    """Whether this request may receive preserved third-party Happ configs."""
    from django.conf import settings
    return (
        getattr(settings, 'SUBSCRIPTION_XRAY_JSON_NATIVE_MIRRORS_ENABLED', False) is True
        and bool(_HAPP_UA_PATTERN.search(user_agent))
    )


def _native_mirror_user_agent() -> str:
    """Return the fixed provider format selector, never the customer's UA."""
    from django.conf import settings
    value = _validated_upstream_user_agent(getattr(
        settings, 'SUBSCRIPTION_XRAY_JSON_NATIVE_MIRROR_USER_AGENT', 'Happ/2.9.0'))
    # A neutral/default agent can select a link list or instruction document.
    # Native preservation is useful only when configuration still identifies a
    # Happ-compatible provider response explicitly.
    return value if _HAPP_UA_PATTERN.search(value) else ''


def _bounded_native_json(value, *, depth: int = 0) -> bool:
    """Bound recursive work and reject control strings in executable JSON."""
    if depth > _NATIVE_PROFILE_MAX_DEPTH:
        return False
    if value is None or isinstance(value, (bool, int, float)):
        return True
    if isinstance(value, str):
        return (
            len(value) <= _NATIVE_PROFILE_MAX_STRING
            and '\x00' not in value and '\r' not in value and '\n' not in value
        )
    if isinstance(value, list):
        return (
            len(value) <= _MIRROR_MAX_DOCUMENT_ENTRIES
            and all(_bounded_native_json(item, depth=depth + 1) for item in value)
        )
    if isinstance(value, dict):
        return (
            len(value) <= 256
            and all(
                isinstance(key, str) and len(key) <= 128
                and _bounded_native_json(item, depth=depth + 1)
                for key, item in value.items()
            )
        )
    return False


def _safe_native_outbound(outbound: dict) -> bool:
    """Validate dial targets while retaining protocol-specific provider fields."""
    protocol = str(outbound.get('protocol', '')).lower()
    if protocol not in ('vless', 'hysteria'):
        return True
    settings = outbound.get('settings')
    if not isinstance(settings, dict):
        return False
    if protocol == 'hysteria':
        host, port = settings.get('address'), _mirror_port(settings.get('port'))
        return isinstance(host, str) and _safe_endpoint_host(host) and port is not None
    vnext = settings.get('vnext')
    if not isinstance(vnext, list) or not 1 <= len(vnext) <= 4:
        return False
    for server in vnext:
        if not isinstance(server, dict):
            return False
        host, port, users = server.get('address'), _mirror_port(server.get('port')), server.get('users')
        if (not isinstance(host, str) or not _safe_endpoint_host(host) or port is None
                or not isinstance(users, list) or not 1 <= len(users) <= 4
                or not all(isinstance(user, dict) and isinstance(user.get('id'), str) and user.get('id')
                           for user in users)):
            return False
    return True


def _safe_native_inbounds(value) -> bool:
    """Accept only the two local client adapters used by known Happ profiles."""
    if not isinstance(value, list) or not value or len(value) > 4:
        return False
    tags = set()
    for inbound in value:
        if not isinstance(inbound, dict):
            return False
        if set(inbound) - {'tag', 'port', 'listen', 'protocol', 'settings', 'sniffing', 'allocate'}:
            return False
        protocol, tag, port = str(inbound.get('protocol', '')).lower(), inbound.get('tag'), inbound.get('port')
        if (protocol not in _NATIVE_PROFILE_ALLOWED_INBOUND_PROTOCOLS
                or not isinstance(tag, str) or not tag or tag in tags
                or type(port) is not int or not 1 <= port <= 65535):
            return False
        listen = inbound.get('listen')
        if listen not in (None, '127.0.0.1', '::1', 'localhost'):
            return False
        tags.add(tag)
    return True


def _native_profile(profile) -> dict | None:
    """Deep-copy one bounded provider profile while retaining its whole graph.

    Tags in Xray selectors are prefixes rather than foreign keys, so validating
    them as exact outbound references would reject the provider's working
    document. We instead validate every executable section's container bounds,
    the complete protocol allowlist, unique explicit tags, and the local-only
    inbound adapters, then preserve DNS/routing/balancers/loops verbatim.
    """
    if (not isinstance(profile, dict) or not profile
            or set(profile) - _NATIVE_PROFILE_ALLOWED_KEYS
            or not _bounded_native_json(profile)):
        return None
    remarks = profile.get('remarks')
    if not isinstance(remarks, str) or not remarks.strip() or len(remarks) > 128:
        return None
    if not _safe_native_inbounds(profile.get('inbounds')):
        return None
    outbounds = profile.get('outbounds')
    if not isinstance(outbounds, list) or not 1 <= len(outbounds) <= _NATIVE_PROFILE_MAX_OUTBOUNDS:
        return None
    tags, endpoint_count = set(), 0
    for outbound in outbounds:
        if not isinstance(outbound, dict):
            return None
        tag, protocol = outbound.get('tag'), str(outbound.get('protocol', '')).lower()
        if (not isinstance(tag, str) or not tag or len(tag) > 256 or tag in tags
                or protocol not in _NATIVE_PROFILE_ALLOWED_PROTOCOLS
                or not _safe_native_outbound(outbound)):
            return None
        tags.add(tag)
        endpoint_count += protocol in ('vless', 'hysteria')
    if endpoint_count == 0:
        return None
    routing = profile.get('routing')
    if not isinstance(routing, dict):
        return None
    balancers, rules = routing.get('balancers'), routing.get('rules')
    if (not isinstance(balancers, list) or len(balancers) > _NATIVE_PROFILE_MAX_BALANCERS
            or not all(isinstance(item, dict) for item in balancers)
            or not isinstance(rules, list) or len(rules) > _NATIVE_PROFILE_MAX_RULES
            or not all(isinstance(item, dict) for item in rules)
            or not isinstance(profile.get('dns'), dict)
            or not isinstance(profile.get('burstObservatory'), dict)):
        return None
    preserved = copy.deepcopy(profile)
    # The provider omits ``listen``, which makes standalone Xray bind these
    # adapters publicly. Happ needs their tags and ports, not LAN exposure.
    for inbound in preserved['inbounds']:
        inbound['listen'] = '127.0.0.1'
    return preserved


def _sanitize_native_profiles(payload: bytes, headers: dict[str, str]) -> list[dict]:
    """Accept a complete bounded Happ array or reject the source atomically."""
    if _upstream_identity_refused(headers):
        raise _UpstreamPlaceholderDocument('upstream_client_identity_placeholder')
    document = _load_upstream_json(payload)
    if not isinstance(document, list) or not 1 <= len(document) <= _NATIVE_PROFILE_MAX_PROFILES:
        return []
    profiles = [_native_profile(profile) for profile in document]
    # Partial preservation silently changes the provider's routing choices.
    return [profile for profile in profiles if profile is not None] if all(profiles) else []


def _cached_native_profiles(url: str, user_agent: str) -> list[dict]:
    """Fetch and cache a provider's complete Happ profiles independently."""
    from django.conf import settings
    source_key = _backup_cache_key(url)
    key = f'{source_key}\x00{hashlib.sha256(user_agent.encode("ascii")).hexdigest()}'
    now = time.monotonic()
    with _BACKUP_CACHE_LOCK:
        cached = _NATIVE_PROFILE_CACHE.get(key)
        if cached and cached[0] > now:
            return copy.deepcopy(cached[1])
        if cached:
            _NATIVE_PROFILE_CACHE.pop(key, None)
        in_flight = _NATIVE_PROFILE_FETCHING.get(key)
        if in_flight is None:
            in_flight = threading.Event()
            _NATIVE_PROFILE_FETCHING[key] = in_flight
            generation = _BACKUP_CACHE_GENERATION
            fetcher = True
        else:
            fetcher = False
    if not fetcher:
        in_flight.wait()
        with _BACKUP_CACHE_LOCK:
            cached = _NATIVE_PROFILE_CACHE.get(key)
            return copy.deepcopy(cached[1]) if cached and cached[0] > time.monotonic() else []
    try:
        response_headers, payload = _fetch_upstream_payload(url, user_agent=user_agent)
        profiles = _sanitize_native_profiles(payload, response_headers)
        if not profiles:
            return []
        ttl = _bounded_number(getattr(settings, 'SUBSCRIPTION_BACKUP_CACHE_TTL_SECONDS', 300),
                              default=300, lower=1, upper=3600)
        with _BACKUP_CACHE_LOCK:
            if generation != _BACKUP_CACHE_GENERATION:
                return []
            _NATIVE_PROFILE_CACHE[key] = (time.monotonic() + ttl, profiles)
            active_keys = {_backup_cache_key(url) for url in getattr(
                settings, 'SUBSCRIPTION_BACKUP_UPSTREAM_URLS', []) if isinstance(url, str)}
        _evict_backup_cache(active_keys)
        return copy.deepcopy(profiles)
    except _UpstreamPlaceholderDocument:
        logger.warning(
            'subscription backup source %s served a client-identification placeholder '
            'instead of native profiles; check the configured client identity', source_key[:12])
        return []
    except (ValueError, UnicodeError, OSError):
        return []
    finally:
        with _BACKUP_CACHE_LOCK:
            _NATIVE_PROFILE_FETCHING.pop(key, None)
            in_flight.set()


def _native_mirror_profiles() -> list[dict] | None:
    """Return all configured native Happ profiles, or decline atomically."""
    from django.conf import settings
    user_agent = _native_mirror_user_agent()
    urls = getattr(settings, 'SUBSCRIPTION_BACKUP_UPSTREAM_URLS', [])
    source_limit = int(_bounded_number(
        getattr(settings, 'SUBSCRIPTION_BACKUP_MAX_SOURCES', 8), default=8, lower=1, upper=32))
    if not user_agent or not isinstance(urls, list):
        return None
    valid_urls = [url for url in urls if isinstance(url, str) and _valid_upstream_url(url)][:source_limit]
    if not valid_urls:
        return None
    _evict_backup_cache({_backup_cache_key(url) for url in valid_urls})
    profiles = []
    for url in valid_urls:
        source_profiles = _cached_native_profiles(url, user_agent)
        if not source_profiles or len(profiles) + len(source_profiles) > _NATIVE_PROFILE_MAX_PROFILES:
            return None
        profiles.extend(source_profiles)
    return profiles or None


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


def _fetch_upstream_payload(url: str, *, user_agent: str | None = None) -> tuple[dict[str, str], bytes]:
    """Fetch identity bytes over TLS pinned to one pre-resolved public IP.

    A single absolute monotonic deadline starts before DNS. Every blocking
    receive is bounded by the remaining time, so a slow-drip response cannot
    extend the request by repeatedly resetting a per-read timeout.

    Response headers travel back with the body because a provider says whether
    it accepted our client identity there, and a document alone cannot be told
    apart from the instructions it serves to a client it does not recognize.

    ``user_agent`` is an internal format selector, never copied from the
    customer's request. It passes through the same printable-ASCII validation
    as the configured default so it cannot add request headers.
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
            f'User-Agent: {_upstream_user_agent() if user_agent is None else _validated_upstream_user_agent(user_agent)}\r\n'
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
# What a customer is allowed to read on a mirrored endpoint.  A provider's own
# label names its product, its panel and its inventory ('→ Remnawave',
# 'SWEDEN_VLESS_1'); a paying customer of this deployment must not learn from a
# subscription which service it resells or how that service numbers its racks.
# So no byte of the provider's label is rendered.  It is read for exactly one
# thing — which country the endpoint sits in — and every character that reaches
# the URI, the flag included, is regenerated here from the ISO 3166-1 alpha-2
# code that signal resolved to.
# The names are Russian because the whole list is: the bot speaks Russian, the
# status line above these entries is Russian, and a customer reading «Подписка»
# and «белые списки» in one list should not find 'Netherlands' between them.
_MIRROR_REGION_NAMES = {
    'AE': 'ОАЭ', 'AM': 'Армения', 'AT': 'Австрия', 'AU': 'Австралия', 'AZ': 'Азербайджан',
    'BE': 'Бельгия', 'BG': 'Болгария', 'BR': 'Бразилия', 'BY': 'Беларусь', 'CA': 'Канада',
    'CH': 'Швейцария', 'CN': 'Китай', 'CY': 'Кипр', 'CZ': 'Чехия', 'DE': 'Германия',
    'DK': 'Дания', 'EE': 'Эстония', 'ES': 'Испания', 'EU': 'Европа', 'FI': 'Финляндия',
    'FR': 'Франция', 'GB': 'Великобритания', 'GE': 'Грузия', 'GR': 'Греция',
    'HK': 'Гонконг', 'HU': 'Венгрия', 'ID': 'Индонезия', 'IE': 'Ирландия', 'IL': 'Израиль',
    'IN': 'Индия', 'IR': 'Иран', 'IS': 'Исландия', 'IT': 'Италия', 'JP': 'Япония',
    'KG': 'Киргизия', 'KR': 'Южная Корея', 'KZ': 'Казахстан', 'LT': 'Литва',
    'LU': 'Люксембург', 'LV': 'Латвия', 'MD': 'Молдова', 'MX': 'Мексика', 'MY': 'Малайзия',
    'NL': 'Нидерланды', 'NO': 'Норвегия', 'NZ': 'Новая Зеландия', 'PL': 'Польша',
    'PT': 'Португалия', 'RO': 'Румыния', 'RS': 'Сербия', 'RU': 'Россия', 'SE': 'Швеция',
    'SG': 'Сингапур', 'SK': 'Словакия', 'TH': 'Таиланд', 'TR': 'Турция', 'TW': 'Тайвань',
    'UA': 'Украина', 'US': 'США', 'UZ': 'Узбекистан', 'VN': 'Вьетнам',
    'ZA': 'ЮАР',
}
# The parse side stays English and is never rendered: a provider writes
# 'SWEDEN_VLESS_1', and ``_label_words`` only collects ASCII runs, so a Cyrillic
# token table would recognize no place a provider actually writes.
_MIRROR_REGION_ENGLISH_NAMES = {
    'AE': 'UAE', 'AM': 'Armenia', 'AT': 'Austria', 'AU': 'Australia', 'AZ': 'Azerbaijan',
    'BE': 'Belgium', 'BG': 'Bulgaria', 'BR': 'Brazil', 'BY': 'Belarus', 'CA': 'Canada',
    'CH': 'Switzerland', 'CN': 'China', 'CY': 'Cyprus', 'CZ': 'Czechia', 'DE': 'Germany',
    'DK': 'Denmark', 'EE': 'Estonia', 'ES': 'Spain', 'EU': 'Europe', 'FI': 'Finland',
    'FR': 'France', 'GB': 'United Kingdom', 'GE': 'Georgia', 'GR': 'Greece',
    'HK': 'Hong Kong', 'HU': 'Hungary', 'ID': 'Indonesia', 'IE': 'Ireland', 'IL': 'Israel',
    'IN': 'India', 'IR': 'Iran', 'IS': 'Iceland', 'IT': 'Italy', 'JP': 'Japan',
    'KG': 'Kyrgyzstan', 'KR': 'South Korea', 'KZ': 'Kazakhstan', 'LT': 'Lithuania',
    'LU': 'Luxembourg', 'LV': 'Latvia', 'MD': 'Moldova', 'MX': 'Mexico', 'MY': 'Malaysia',
    'NL': 'Netherlands', 'NO': 'Norway', 'NZ': 'New Zealand', 'PL': 'Poland',
    'PT': 'Portugal', 'RO': 'Romania', 'RS': 'Serbia', 'RU': 'Russia', 'SE': 'Sweden',
    'SG': 'Singapore', 'SK': 'Slovakia', 'TH': 'Thailand', 'TR': 'Turkey', 'TW': 'Taiwan',
    'UA': 'Ukraine', 'US': 'United States', 'UZ': 'Uzbekistan', 'VN': 'Vietnam',
    'ZA': 'South Africa',
}
# Forms a provider writes that are not the name above.
_MIRROR_REGION_ALIASES = {
    'USA': 'US', 'UK': 'GB', 'ENGLAND': 'GB', 'KOREA': 'KR', 'HOLLAND': 'NL',
    'EMIRATES': 'AE', 'TURKIYE': 'TR', 'CZECHREPUBLIC': 'CZ',
}
_MIRROR_REGION_TOKENS = {
    **{name.upper().replace(' ', ''): code
       for code, name in _MIRROR_REGION_ENGLISH_NAMES.items()},
    **{code: code for code in _MIRROR_REGION_NAMES},
    **_MIRROR_REGION_ALIASES,
}
# An endpoint whose region we cannot name is still worth offering; naming it
# after the provider's own string is the part we refuse.
_MIRROR_UNKNOWN_REGION = '🌐 Резерв'
# What the suffix promises a customer, which is the one thing that is not
# interchangeable between two lines of the same country: this line still works
# when a region's mobile internet is cut to a whitelist.  Two different
# endpoints qualify and the customer must not have to learn two words for one
# property.  Ours is the RU relay in ``Server.client_vpn_host`` — a Russian
# ingress reachable from a whitelisted network that refuses the direct host.  A
# mirrored one is a Russian ingress announcing a Reality ``server_name`` an
# operator declared as whitelist camouflage.  The suffix is composed from that
# property of the endpoint, never written onto a line.
_WHITELIST_LABEL_SUFFIX = 'белые списки'
# То же для второго свойства, которым одна линия отличается от другой линии той
# же страны: она несёт трафик обычными HTTPS-запросами к подписочному имени, а не
# отдельным TCP-потоком на выделенный порт.  Провайдер, научившийся опознавать
# наш Reality-хендшейк, эту линию не отличает от посещения сайта — поэтому она и
# нужна ровно тогда, когда основная перестала работать.  Клиенту сказано именно
# это, без слова о транспорте: «запасной путь» — то, что он выберет, когда
# первая строка молчит.
_ALT_TRANSPORT_LABEL_SUFFIX = 'запасной путь'
# Третья линия того же узла. Клиенту снова не сказано ни слова о транспорте —
# только то, чем эта строка отличается для него: пробовать её стоит, когда
# замолчали и основная, и запасная. Слово выбрано так, чтобы порядок на экране
# читался как порядок попыток, а не как выбор из трёх равных.
_GRPC_LABEL_SUFFIX = 'обходной путь'
# A configured suffix list is operator input, not provider input, but it is
# still read once per rendered document, so its length is bounded like every
# other list this module walks.
_MIRROR_MAX_WHITELIST_SUFFIXES = 16
# How many liveness verdicts one selection may read.  A provider document
# carries at most ``_MIRROR_MAX_ENDPOINTS`` endpoints and eight sources may be
# configured, so this is the whole probeable fleet with room to spare.
_MIRROR_LIVENESS_MAX_ROWS = 1024
# Every endpoint this deployment runs itself exits in the Netherlands, the relay
# included: the RU front is a byte-transparent stream relay, not another exit.
_OWN_REGION_CODE = 'NL'
# Public addresses that answer on 443 and are not anybody's VPN server.  A
# provider listed 1.1.1.1 under its own '🇪🇺 Fastest' label; every region we
# rendered then pointed a customer at an anycast resolver, which accepts the TCP
# connection and never completes a handshake, so a whole subscription looked
# alive and worked nowhere.  A label is not evidence about what listens, so
# these are refused whatever the provider calls them.  Private, loopback,
# link-local, reserved and documentation ranges are already refused by
# ``_is_public_unicast``; these are ordinary global unicast and must be named.
_MIRROR_EXCLUDED_HOSTS = frozenset(ipaddress.ip_address(address) for address in (
    '1.1.1.1', '1.0.0.1', '2606:4700:4700::1111', '2606:4700:4700::1001',
    '8.8.8.8', '8.8.4.4', '2001:4860:4860::8888', '2001:4860:4860::8844',
    '9.9.9.9', '149.112.112.112', '2620:fe::fe', '2620:fe::9',
    '77.88.8.8', '77.88.8.1', '2a02:6b8::feed:0ff', '2a02:6b8:0:1::feed:0ff',
    '208.67.222.222', '208.67.220.220', '2620:119:35::35', '2620:119:53::53',
))
_REGIONAL_INDICATOR_A = 0x1F1E6


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


def _validated_upstream_user_agent(value) -> str:
    """Return one printable format selector or the neutral safe default."""
    if not isinstance(value, str):
        return _DEFAULT_UPSTREAM_USER_AGENT
    value = value.strip()
    if not value or len(value) > 128 or not all(' ' <= character <= '~' for character in value):
        return _DEFAULT_UPSTREAM_USER_AGENT
    return value


def _upstream_user_agent() -> str:
    """Return the agent this deployment presents to providers.

    Some providers serve a different document per client User-Agent and refuse
    unknown ones, so the agent is what selects the machine-readable format. An
    unset, oversized or non-printable value keeps the neutral default rather
    than letting configuration write arbitrary bytes into a request header.
    """
    from django.conf import settings
    return _validated_upstream_user_agent(
        getattr(settings, 'SUBSCRIPTION_BACKUP_UPSTREAM_USER_AGENT', ''))


def _sanitize_upstream_payload(payload: bytes, headers: dict[str, str] | None = None) -> list[str]:
    """Decode payload framing while retaining accepted proxy lines exactly."""
    structured = _structured_upstream_links(payload, headers)
    if structured is not None:
        return structured
    decoded = _decode_subscription_payload(payload)
    links = []
    for raw_line in decoded.splitlines():
        if raw_line.startswith(b'vless://'):
            if not _is_sentinel_vless_line(raw_line):
                links.append(raw_line.decode('utf-8'))
            continue
        if raw_line.startswith((b'hy2://', b'hysteria2://')) and _valid_raw_hysteria_line(raw_line):
            links.append(raw_line.decode('utf-8'))
    return links


def _valid_raw_hysteria_line(raw_line: bytes) -> bool:
    """Accept one complete Hysteria2 URI without normalising bearer bytes."""
    try:
        line = raw_line.decode('utf-8')
        parts = urlsplit(line)
        query = {key: values[0] for key, values in parse_qs(parts.query).items()}
    except (UnicodeDecodeError, ValueError):
        return False
    if parts.scheme not in ('hy2', 'hysteria2'):
        return False
    if not parts.username or not parts.hostname or parts.port is None or not query.get('sni'):
        return False
    return not any(character in value for value in (parts.username, parts.hostname, query['sni'])
                   for character in ' \r\n\t#?/@')


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

    Parsing is separate from selection because the prober needs every endpoint
    a provider offers, while a customer receives the slice this picks.
    """
    endpoints = _parse_upstream_endpoints(payload, headers)
    if endpoints is None:
        return None
    links = []
    for endpoint in _branded_mirror_endpoints(endpoints):
        links.append(_build_mirror_vless(endpoint))
        hysteria = _mirror_hysteria_link(endpoint)
        if hysteria:
            links.append(hysteria)
    return links


def _parse_upstream_endpoints(payload: bytes,
                              headers: dict[str, str] | None = None) -> list[dict] | None:
    """Normalize every servable endpoint in a JSON provider document, or decline it.

    ``None`` means "not a document I parse" and hands the bytes back to the
    opaque URI-list path unchanged; an empty list means the document parsed and
    offered nothing servable. Callers must keep that distinction, otherwise a
    provider that answers YAML would silently look like a provider with no
    endpoints.

    Response headers are optional because the same parse runs on bytes alone in
    tests and on the opaque path. When they are present they decide one further
    case: a document that is really a message to the user, which must not be
    counted as a source with nothing to offer.

    Nothing here is capped per region: this is every endpoint the provider
    offers, which is what the prober has to dial. ``_branded_mirror_endpoints``
    applies the per-region cap within one document, which is where a region is
    known at all, and ``_backup_links`` caps the mirrored total across sources.
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
    endpoints = []
    for raw_endpoint in raw_endpoints[:_MIRROR_MAX_ENDPOINTS]:
        endpoint = _normalized_mirror_endpoint(raw_endpoint)
        if endpoint is None:
            continue
        if endpoint['security'] not in _MIRROR_SECURE_TRANSPORTS and not allow_plaintext:
            continue
        endpoints.append(endpoint)
    return endpoints


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
    # The provider's labels are read for one thing: which country this endpoint
    # sits in.  The server's own tag is tried before its group's, because a
    # group may be the provider's root selector, which names the provider and
    # not a place.  Both are bounded and control-character-free before they are
    # read, and what reaches the URI is text this module composed, so a label
    # can no longer lengthen a remark, name the upstream, or carry inventory
    # numbering into a customer's client.
    region = _mirror_region_code(
        _mirror_field(raw.get('remark')), _mirror_field(raw.get('region'), limit=64))
    # Whether this endpoint keeps working while a region is cut to a whitelist.
    # Read from the announced Reality server name against the operator's own
    # list, never from the provider's tag text: the audited document names eight
    # outbounds ``BRIDGE_*`` and each is a byte-identical twin of its plain
    # counterpart, same address and same exit.
    server_name = _mirror_field(raw.get('server_name'), limit=253)
    whitelisted = _whitelist_capable(server_name)
    label = _endpoint_label(region, whitelisted=whitelisted)
    endpoint = {
        'host': host,
        'port': port,
        'region_code': region,
        'whitelisted': whitelisted,
        # Our own UUIDs are quote-invariant, so quoting costs nothing here and
        # stops a provider from writing URI structure into the userinfo field.
        'uuid': quote(uuid, safe=''),
        'label': label,
        'remark': label,
        'flow': _mirror_field(raw.get('flow'), limit=64),
        'security': security,
        'public_key': _mirror_field(raw.get('public_key')),
        'short_id': _mirror_field(raw.get('short_id'), limit=64),
        'server_name': server_name,
        'fingerprint': _mirror_field(raw.get('fingerprint'), limit=64),
        'network': _mirror_field(raw.get('network'), limit=32).lower() or 'tcp',
        'service_name': _mirror_field(raw.get('service_name')),
        'path': _mirror_field(raw.get('path')),
    }
    # Reality without its public key is not a usable endpoint, and rendering it
    # would advertise a Reality link that no client can complete. The short id
    # is optional: a server may be configured with an empty ``shortIds`` list,
    # and clients then simply omit ``sid``.
    if security == 'reality' and not endpoint['public_key']:
        return None
    return endpoint


def _region_flag(code: str) -> str:
    """Compose the flag for an alpha-2 code out of regional indicator letters."""
    return ''.join(chr(_REGIONAL_INDICATOR_A + ord(letter) - ord('A')) for letter in code)


def _flag_region_code(label: str) -> str:
    """Return the alpha-2 code of the first flag emoji in a label, or ''."""
    previous = ''
    for character in label:
        index = ord(character) - _REGIONAL_INDICATOR_A
        letter = chr(ord('A') + index) if 0 <= index < 26 else ''
        if letter and previous:
            return previous + letter
        previous = letter
    return ''


def _label_words(label: str) -> list[tuple[str, bool]]:
    """Split a label into ASCII letter runs, keeping whether each was uppercase."""
    words, current = [], ''
    for character in f'{label} ':
        if character.isascii() and character.isalpha():
            current += character
            continue
        if current:
            words.append((current.upper(), current.isupper()))
            current = ''
    return words


def _named_region_code(label: str) -> str:
    """Resolve a place a provider wrote in words, or ''.

    Two adjacent runs are tried before a single one so 'Hong Kong' and 'United
    States' resolve at all.  A bare two-letter token resolves only when the
    provider already wrote it in capitals, which is what stops an ordinary
    English word in a sentence ('It', 'In', 'Is') from becoming Italy.
    """
    words = _label_words(label)
    for index, (word, capitalized) in enumerate(words):
        pair = word + words[index + 1][0] if index + 1 < len(words) else ''
        for candidate in (pair, word):
            if not candidate or (len(candidate) == 2 and not capitalized):
                continue
            code = _MIRROR_REGION_TOKENS.get(candidate)
            if code:
                return code
    return ''


def _mirror_region_code(*labels: str) -> str:
    """Read a country signal out of a provider's labels, most specific first."""
    for label in labels:
        code = _flag_region_code(label) or _named_region_code(label)
        if code:
            return code
    return ''


def _endpoint_label(code: str, *, whitelisted: bool = False) -> str:
    """Compose the only text a customer reads for one endpoint.

    Two facts and nothing else: where it exits, and whether it survives a
    whitelist.  A country alone would leave the customer choosing between two
    lines that are not substitutes — the suffixed one keeps working from a
    network that refuses the direct host, and is slower for everyone else.

    The country is always the *exit* country, suffix or not.  A Russian ingress
    with camouflaged SNI exits at its own Russian address, so reading its line
    as a foreign one would be a lie about the only thing the flag claims.
    """
    region = f'{_region_flag(code)} {_MIRROR_REGION_NAMES.get(code, code)}' if code \
        else _MIRROR_UNKNOWN_REGION
    return f'{region} {_WHITELIST_LABEL_SUFFIX}' if whitelisted else region


def _whitelist_capable(server_name: str) -> bool:
    """Whether an operator declared this endpoint's Reality SNI as camouflage.

    A domain that stays reachable while a region's mobile internet is cut to a
    whitelist — a large retailer, so payments and shops keep working — lets a
    Reality handshake announcing it through inspection that blocks everything
    else.  That is the whole mechanism: the provider's routing config has three
    rules and no domain logic, so nothing but ``tls.server_name`` distinguishes
    a bypass node from an ordinary one.

    Only a configured suffix is evidence.  Matching is on domain labels rather
    than on raw text, so declaring ``x5.ru`` accepts ``id.x5.ru`` and refuses
    ``notx5.ru``, which is a different registration owned by somebody else.
    """
    from django.conf import settings
    suffixes = getattr(settings, 'SUBSCRIPTION_BACKUP_WHITELIST_SNI_SUFFIXES', [])
    if not server_name or not isinstance(suffixes, list):
        return False
    name = server_name.casefold().strip('.')
    for suffix in suffixes[:_MIRROR_MAX_WHITELIST_SUFFIXES]:
        if not isinstance(suffix, str):
            continue
        candidate = suffix.casefold().strip().strip('.')
        if candidate and (name == candidate or name.endswith(f'.{candidate}')):
            return True
    return False


def _branded_mirror_endpoints(endpoints: list[dict]) -> list[dict]:
    """Order one document's endpoints stably and keep a slice of each region.

    Both the order and the selection are computed from the endpoint's own
    fields and never from its position in the provider's document, so a
    provider that reorders its outbounds between two refreshes cannot reshuffle
    a customer's list.  Ordering is by the rendered country name, so the list
    reads alphabetically in the language it is written in, and the unnamed group
    is held last because it names no country to sort among them.

    Inside a region an endpoint known to answer wins over one nobody has
    dialled, then the least widely shared host wins, and ``(host, port)`` still
    separates hosts shared equally often.  A host a provider offers under nine
    different flags is a front or an aggregate rather than the server of any one
    of those countries, while a host that appears under one flag only is what
    that flag names.  Spread is counted over the whole document rather than over
    the survivors, so what a host *is* stays a property of what the provider
    sent, and liveness only decides which candidates remain and in what order.

    A dead verdict removes the candidate outright, so the next one takes the
    region's slot and a region with nothing left disappears instead of
    rendering a line that connects and never carries traffic.  With no verdicts
    the ranks are equal and every tie falls through to the ordering above,
    which is exactly the selection this shipped with.

    A whitelist-capable endpoint is not counted against the per-region limit.
    It is not a substitute for the region's ordinary server — it is the line
    that still works when only whitelists do — so letting the two compete for
    one slot is what kept it out of a subscription until now.  It sorts after
    the region's ordinary lines for the same reason our own relay sits under
    our own direct line: the ordinary one is what a customer wants first.
    """
    from django.conf import settings
    limit = int(_bounded_number(
        getattr(settings, 'SUBSCRIPTION_BACKUP_MAX_ENTRIES_PER_REGION', 1),
        default=1, lower=1, upper=_MIRROR_MAX_ENDPOINTS))
    verdicts = _liveness_verdicts()
    spread = _mirror_host_spread(endpoints)
    live = [endpoint for endpoint in endpoints
            if verdicts.get((endpoint['host'], endpoint['port']), True)]
    selected, counts = [], {}
    for endpoint in sorted(live, key=lambda item: (_region_order_key(item['region_code']),
                                                   item['whitelisted'],
                                                   not verdicts.get((item['host'], item['port'])),
                                                   spread[item['host']],
                                                   item['host'], item['port'])):
        if endpoint['whitelisted']:
            selected.append(endpoint)
            continue
        position = counts.get(endpoint['label'], 0)
        if position >= limit:
            continue
        counts[endpoint['label']] = position + 1
        selected.append(endpoint)
    return _numbered_mirror_endpoints(selected)


def _liveness_verdicts() -> dict[tuple[str, int], bool]:
    """Return the fresh out-of-band verdicts, or nothing at all.

    Every uncertain case returns an empty mapping — the flag is off, the prober
    has never run, its rows have aged past the configured window, or the query
    itself failed.  Selection then behaves exactly as it did before liveness
    existed, because a feature that silently empties a customer's list when a
    background job stalls is worse than the gap it closes.

    Verdicts reach a customer no faster than
    ``SUBSCRIPTION_BACKUP_CACHE_TTL_SECONDS``, since the rendered lines of one
    source are cached for that long.  That is the trade for never dialling
    inside a request, and it is why a verdict's own window is much wider.
    """
    from django.conf import settings
    if getattr(settings, 'SUBSCRIPTION_BACKUP_LIVENESS_ENABLED', False) is not True:
        return {}
    max_age = _bounded_number(
        getattr(settings, 'SUBSCRIPTION_BACKUP_LIVENESS_MAX_AGE_SECONDS', 3600),
        default=3600, lower=60, upper=86400)
    horizon = timezone.now() - datetime.timedelta(seconds=max_age)
    try:
        return {
            (host, port): alive
            for host, port, alive in MirrorEndpointLiveness.objects.filter(
                checked_at__gte=horizon).values_list(
                    'host', 'port', 'alive')[:_MIRROR_LIVENESS_MAX_ROWS]
        }
    except Exception:
        # A database that cannot answer must cost a customer the improvement,
        # never the subscription: the caller is one query away from a 500 on
        # every refresh otherwise.
        logger.warning('mirror liveness lookup failed; selection falls back to the blind choice')
        return {}


def _numbered_mirror_endpoints(endpoints: list[dict]) -> list[dict]:
    """Number the lines a customer would otherwise be unable to tell apart.

    The count starts at the lines this deployment always renders itself, so a
    mirrored Dutch endpoint reads '🇳🇱 Нидерланды 2' against our own bare
    '🇳🇱 Нидерланды' rather than repeating it: two identical entries leave a
    customer unable to choose between them, and unable to tell that one of them
    failing means something different from the other failing.  Ours keeps the
    bare label because the view renders it first, above every mirrored line.

    A number is a disambiguator, never a category.  Nothing here may say whose
    endpoint a line is: a word like 'Резерв' on this line would leak the thing
    the region-label policy exists to hide, and would tell a customer to
    distrust an endpoint that works.
    """
    counts = {label: 1 for label in _own_rendered_labels()}
    for endpoint in endpoints:
        position = counts.get(endpoint['label'], 0)
        counts[endpoint['label']] = position + 1
        if position:
            endpoint['remark'] = f"{endpoint['label']} {position + 1}"
    return endpoints


def _own_rendered_labels() -> tuple[str, ...]:
    """The labels this deployment's own endpoints occupy in every response.

    Constants, not per-request state: the direct line is unconditional and the
    whitelist-suffixed one is the only other label we compose, which is what
    lets the numbering account for them inside a per-source cache.
    """
    return _endpoint_label(_OWN_REGION_CODE), _endpoint_label(_OWN_REGION_CODE, whitelisted=True)


def _region_order_key(code: str) -> tuple[bool, str]:
    """Order regions the way the customer's alphabet does, unnamed last.

    The rendered label cannot order the list: it opens with the flag, whose code
    points follow the ISO alpha-2 code, so sorting on it spelled the list in ISO
    order — readable while the names were English and coincidentally alphabetic,
    arbitrary once they are Russian (🇫🇮 Финляндия ahead of 🇬🇧 Великобритания).
    Sorting on the name itself is stable for the same reason the label was: the
    name is a function of the code alone.
    """
    return not code, _MIRROR_REGION_NAMES.get(code, code)


def _mirror_host_spread(endpoints: list[dict]) -> dict[str, int]:
    """Count how many distinct regions each host is offered under."""
    regions: dict[str, set] = {}
    for endpoint in endpoints:
        regions.setdefault(endpoint['host'], set()).add(endpoint['label'])
    return {host: len(labels) for host, labels in regions.items()}


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
    """Reject hosts that are not a third party's own server.

    Two refusals for two different failures. A private, loopback or reserved
    literal would aim a client at this deployment's own network; a public
    resolver or anycast address is perfectly reachable and simply has no VPN
    behind it, which renders an entry that connects and then hangs.
    """
    if any(character in host for character in ' \t/@?#') or host.casefold() == 'localhost':
        return False
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        return True
    return _is_public_unicast(str(literal)) and literal not in _MIRROR_EXCLUDED_HOSTS


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
        if endpoint['short_id']:
            query_fields.append(('sid', endpoint['short_id']))
        query_fields.append(('spx', '/'))
    if endpoint['network'] == 'grpc' and endpoint['service_name']:
        query_fields.append(('serviceName', endpoint['service_name']))
    if endpoint['network'] == 'ws' and endpoint['path']:
        query_fields.append(('path', endpoint['path']))
    query = urlencode(query_fields, quote_via=quote)
    return (f"vless://{endpoint['uuid']}@{endpoint['host']}:{endpoint['port']}"
            f"?{query}#{quote(endpoint['remark'])}")


def _mirror_hysteria_link(endpoint: dict) -> str | None:
    """Render the same mirrored country over hysteria2, or nothing.

    Почему это отдельная строка, а не поле в предыдущей: провайдер держит на
    каждой своей стране каскад — прямой VLESS, затем hysteria2 на другом порту,
    затем тот же VLESS через российский мост. В его собственном приложении
    ступени переключаются сами, а ``vless://`` описывает ровно одну точку и
    цепочку выразить не может. Вторая ступень выражается: это отдельный адрес и
    отдельный протокол, то есть отдельная ссылка.

    Замерено 2026-08-20 из белорусской сети: прямой VLESS не поднялся ни на
    одной из девяти стран, hysteria2 поднялся на восьми. Строка не заменяет
    первую, а идёт следом — где TCP жив (мобильные сети), первая по-прежнему
    быстрее, и клиент выбирает сам.

    Порт и то, что паролем служит тот же UUID, — свойство этого провайдера, а
    не протокола, поэтому живут в настройке: другой провайдер того же формата
    получит здесь ноль и ни одной лишней строки.
    """
    from django.conf import settings
    port = getattr(settings, 'SUBSCRIPTION_BACKUP_HYSTERIA_PORT', 0)
    if not isinstance(port, int) or not 1 <= port <= 65535:
        return None
    uuid = endpoint.get('uuid')
    host = endpoint.get('host')
    server_name = endpoint.get('server_name')
    remark = endpoint.get('remark')
    if not all(isinstance(value, str) and value
               for value in (uuid, host, server_name, remark)):
        return None
    # Тот же набор запретов, что у остальных строк: символ, ломающий URI,
    # превращает строку в мусор у клиента, а не в ошибку здесь.
    if any(character in value for value in (uuid, host, server_name)
           for character in ' \r\n\t#?/@'):
        return None
    # Отпечаток берётся у той же точки, а не подставляется умолчанием: провайдер
    # объявляет свои узлы под ``firefox``, и наш ``chrome`` был единственным
    # полем, которым эта ступень отличалась от той, что у того же клиента
    # работает. uTLS-отпечаток — это то, как выглядит хендшейк на проводе, так
    # что расхождение здесь не косметическое.
    fingerprint = endpoint.get('fingerprint')
    fields = [('sni', server_name), ('alpn', 'h3')]
    if isinstance(fingerprint, str) and fingerprint and not any(
            character in fingerprint for character in ' \r\n\t#?/@'):
        fields.append(('fp', fingerprint))
    query = urlencode(fields, quote_via=quote)
    label = f'{remark} {_ALT_TRANSPORT_LABEL_SUFFIX}'
    return f'hy2://{uuid}@{host}:{port}/?{query}#{quote(label)}'


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
        if literal and (literal.is_private or literal.is_loopback or literal.is_unspecified
                        or literal in _MIRROR_EXCLUDED_HOSTS):
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