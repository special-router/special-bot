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
import time
from functools import lru_cache

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
        return HttpResponseNotFound()

    if not user_vpn.enabled:
        return HttpResponseNotFound()

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
    backup_links = _backup_links(
        user_vpn.id, uuid_str,
    ) if _is_backup_test_user(user_vpn.id) else None
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
    return resp


def _endpoint(client_vpn_host: str, default_port: int) -> tuple[str, int]:
    host, sep, port = client_vpn_host.rpartition(':')
    if sep and port.isdigit():
        return host, int(port)
    return client_vpn_host, default_port


def _is_backup_test_user(user_vpn_id: int) -> bool:
    from django.conf import settings
    if not getattr(settings, 'SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED', False):
        return False
    test_ids = getattr(settings, 'SUBSCRIPTION_BACKUP_TEST_USER_IDS', []) or []
    # Empty allowlist during rollout = no one receives backups yet.
    return bool(test_ids) and user_vpn_id in test_ids


def _backup_links(user_vpn_id: int, uuid_str: str) -> list[str] | None:
    """Render external backup service VLESS links for the test group.

    Backup endpoints are external VPN services (MORI, etc.), NOT our own
    3x-ui ports. Each entry is a static, operator-provisioned VLESS Reality
    endpoint. See docs/MIRROR-INBOUNDS-SPEC.md.
    """
    from django.conf import settings
    if not getattr(settings, 'SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED', False):
        return None
    endpoints = getattr(settings, 'SUBSCRIPTION_BACKUP_ENDPOINTS', []) or []
    if not endpoints:
        return None
    links = []
    for ep in endpoints:
        params = {
            'public_key': ep['pbk'],
            'server_name': ep['sni'],
            'short_ids': [ep['sid']],
            'port': ep['port'],
            'network': ep.get('type', 'tcp'),
            'inbound_id': 0,
        }
        links.append(_build_vless(
            ep['uuid'], ep['host'], ep['port'], ep['label'],
            params, flow=ep.get('flow', ''),
            fingerprint=ep.get('fp', 'chrome'),
        ))
    return links


def settings_relays():
    from django.conf import settings
    return settings