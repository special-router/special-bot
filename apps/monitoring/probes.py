"""Secret-safe SPECIAL control-plane, regional and protocol probes."""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import os
import socket
import subprocess
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from django.conf import settings

from apps.servers.management.commands.audit_xui_inbounds import fetch_inbound_snapshots
from apps.servers.models import Server
from apps.servers.subscription_connector import build_subscription_url
from apps.vpn.management.commands.audit_legacy_vpn import fetch_control_plane_client_ids, get_server_entitlement
from apps.vpn.models import UserVPN
from utils.py3xui.async_api import AsyncApi


@dataclass(frozen=True)
class LayerResult:
    layer: str
    ok: bool
    error_class: str | None
    immediate: bool = False
    details: dict[str, object] | None = None

    def as_safe_dict(self) -> dict[str, object]:
        value = asdict(self)
        value['details'] = self.details or {}
        return value


@dataclass(frozen=True)
class EndpointProbe:
    name: str
    target_region: str
    port: int
    transport: str
    ok: bool
    latency_ms: float | None
    error_class: str | None


def probe_tcp(host: str, port: int, timeout: float) -> tuple[bool, float | None, str | None]:
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, round((time.monotonic() - started) * 1000, 1), None
    except OSError as error:
        return False, None, type(error).__name__


def run_regional_probe() -> LayerResult:
    results = []
    for endpoint in settings.SPECIAL_MONITOR_ENDPOINTS:
        ok, latency, error_class = probe_tcp(endpoint['host'], int(endpoint['port']), 5.0)
        results.append(
            EndpointProbe(
                name=endpoint['name'],
                target_region=endpoint['target_region'],
                port=int(endpoint['port']),
                transport=endpoint['transport'],
                ok=ok,
                latency_ms=latency,
                error_class=error_class,
            )
        )
    if not results:
        return LayerResult(layer='l1', ok=False, error_class='not_configured')
    return LayerResult(
        layer='l1',
        ok=all(result.ok for result in results),
        error_class=None if all(result.ok for result in results) else 'regional_reachability',
        details={
            'probe_region': settings.SPECIAL_MONITOR_PROBE_REGION,
            'endpoints': [asdict(item) for item in results],
        },
    )


def run_control_plane_probe() -> LayerResult:
    inbound_rows = []
    legacy_rows = []
    missing_total = 0
    inventory_drift = False
    servers = list(Server.objects.select_related('tariff').order_by('id'))
    if not servers:
        return LayerResult(layer='l0', ok=False, error_class='not_configured')

    try:
        for server in servers:
            snapshots = asyncio.run(fetch_inbound_snapshots(server))
            for item in snapshots:
                row = {
                    'server_id': item.server_id,
                    'inbound_id': item.inbound_id,
                    'port': item.port,
                    'protocol': item.protocol,
                    'network': item.network,
                    'security': item.security,
                    'clients': item.clients,
                    'enabled_clients': item.enabled_clients,
                    'with_sub_id': item.with_sub_id,
                    'missing_sub_id': item.missing_sub_id,
                }
                inbound_rows.append(row)
                expected = next(
                    (
                        expected
                        for expected in settings.SPECIAL_MONITOR_EXPECTED_INBOUNDS
                        if int(expected.get('server_id', -1)) == item.server_id
                        and int(expected.get('inbound_id', -1)) == item.inbound_id
                    ),
                    None,
                )
                if expected and any(
                    row[key] != expected[key] for key in ('port', 'protocol', 'network', 'security') if key in expected
                ):
                    inventory_drift = True
            records, entitled_ids = get_server_entitlement(server)
            control_ids, enabled_ids = asyncio.run(fetch_control_plane_client_ids(server))
            missing = len(entitled_ids - enabled_ids)
            missing_total += missing
            legacy_rows.append(
                {
                    'server_id': server.id,
                    'records': records,
                    'entitled': len(entitled_ids),
                    'control_plane': len(control_ids),
                    'control_plane_enabled': len(enabled_ids),
                    'entitled_missing': missing,
                    'extras': len(control_ids - entitled_ids),
                }
            )
        expected_keys = {
            (int(expected['server_id']), int(expected['inbound_id']))
            for expected in settings.SPECIAL_MONITOR_EXPECTED_INBOUNDS
        }
        actual_keys = {(row['server_id'], row['inbound_id']) for row in inbound_rows}
        inventory_drift = inventory_drift or not expected_keys.issubset(actual_keys)
    except Exception:
        return LayerResult(layer='l0', ok=False, error_class='control_plane')

    return LayerResult(
        layer='l0',
        ok=missing_total == 0 and not inventory_drift,
        error_class=('entitled_missing' if missing_total else 'inbound_inventory_drift' if inventory_drift else None),
        immediate=missing_total > 0 or inventory_drift,
        details={
            'inbounds': inbound_rows,
            'legacy': legacy_rows,
            'inventory_drift': inventory_drift,
        },
    )


async def get_canary_subscription(user_vpn: UserVPN) -> str:
    api = AsyncApi(
        user_vpn.server.vpn_url,
        user_vpn.server.vpn_username,
        user_vpn.server.vpn_password,
        use_tls_verify=False,
    )
    await api.login()
    inbound = await api.inbound.get_by_id(user_vpn.server.inbound_id)
    matches = [client for client in inbound.settings.clients if str(client.id) == str(user_vpn.vpn_uuid)]
    if len(matches) != 1 or not matches[0].sub_id:
        raise RuntimeError('canary_client_missing')
    return build_subscription_url(settings.SUBSCRIPTION_BASE_URL, matches[0].sub_id)


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, msg, headers, new_url):
        raise RuntimeError('subscription_redirect')


_no_redirect_opener = urllib.request.build_opener(NoRedirectHandler)


def fetch_subscription_entry(url: str, expected_uuid: str) -> str:
    request = urllib.request.Request(url, headers={'User-Agent': 'SPECIAL-production-canary/1'})
    with _no_redirect_opener.open(request, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError('subscription_http')
        payload = response.read(1024 * 1024)
    decoded = base64.b64decode(b''.join(payload.split()), validate=True).decode('utf-8')
    links = [line.strip() for line in decoded.splitlines() if line.strip()]
    if not links:
        raise RuntimeError('subscription_payload')
    # A subscription may expose several endpoints (status, direct, relay); pick
    # the first working VLESS entry whose client UUID matches the canary and
    # whose host is not a loopback info-only endpoint.
    for link in links:
        if not link.startswith('vless://'):
            continue
        parsed = urllib.parse.urlsplit(link)
        if urllib.parse.unquote(parsed.username or '') != expected_uuid:
            continue
        host = (parsed.hostname or '').lower()
        if host in {'127.0.0.1', 'localhost', '::1'}:
            continue
        return link
    raise RuntimeError('subscription_client')


def query_value(query: dict[str, list[str]], key: str, default: str = '') -> str:
    return query.get(key, [default])[0]


def build_xray_config(link: str, port: int) -> dict[str, object]:
    parsed = urllib.parse.urlsplit(link)
    query = urllib.parse.parse_qs(parsed.query)
    user = {'id': urllib.parse.unquote(parsed.username or ''), 'encryption': query_value(query, 'encryption', 'none')}
    if query_value(query, 'flow'):
        user['flow'] = query_value(query, 'flow')
    stream: dict[str, object] = {
        'network': query_value(query, 'type', 'tcp'),
        'security': query_value(query, 'security', 'none'),
    }
    if stream['security'] == 'reality':
        stream['realitySettings'] = {
            'show': False,
            'serverName': query_value(query, 'sni'),
            'fingerprint': query_value(query, 'fp', 'chrome'),
            'publicKey': query_value(query, 'pbk'),
            'shortId': query_value(query, 'sid'),
            'spiderX': query_value(query, 'spx', '/'),
        }
    elif stream['security'] == 'tls':
        stream['tlsSettings'] = {
            'serverName': query_value(query, 'sni'),
            'fingerprint': query_value(query, 'fp', 'chrome'),
            'allowInsecure': False,
        }
    if stream['network'] == 'grpc':
        stream['grpcSettings'] = {
            'serviceName': query_value(query, 'serviceName'),
            'authority': query_value(query, 'authority'),
            'multiMode': query_value(query, 'mode') == 'multi',
        }
    elif stream['network'] == 'ws':
        stream['wsSettings'] = {
            'path': query_value(query, 'path', '/'),
            'headers': {'Host': query_value(query, 'host')} if query_value(query, 'host') else {},
        }
    return {
        'log': {'loglevel': 'warning'},
        'inbounds': [{'listen': '127.0.0.1', 'port': port, 'protocol': 'socks', 'settings': {'udp': False}}],
        'outbounds': [
            {
                'protocol': 'vless',
                'settings': {'vnext': [{'address': parsed.hostname, 'port': parsed.port or 443, 'users': [user]}]},
                'streamSettings': stream,
            }
        ],
    }


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return int(listener.getsockname()[1])


def wait_port(process: subprocess.Popen[bytes], port: int) -> bool:
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        with socket.socket() as client:
            client.settimeout(0.2)
            if client.connect_ex(('127.0.0.1', port)) == 0:
                return True
        if process.poll() is not None:
            return False
        time.sleep(0.1)
    return False


def child_environment() -> dict[str, str]:
    return {
        'HOME': '/tmp',
        'LANG': os.environ.get('LANG', 'C.UTF-8'),
        'PATH': '/usr/local/bin:/usr/bin:/bin',
        'SSL_CERT_DIR': '/etc/ssl/certs',
    }


def run_vless(link: str, xray_path: Path, expected_egress: str) -> bool:
    port = free_port()
    environment = child_environment()
    process = subprocess.Popen(
        [str(xray_path), 'run', '-c', 'stdin:'],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
    )
    assert process.stdin is not None
    process.stdin.write(json.dumps(build_xray_config(link, port)).encode())
    process.stdin.close()
    try:
        if not wait_port(process, port):
            return False
        request = subprocess.run(
            [
                'curl',
                '-fsS',
                '--socks5-hostname',
                f'127.0.0.1:{port}',
                '--connect-timeout',
                '10',
                '--max-time',
                '22',
                settings.SPECIAL_MONITOR_HEALTH_URL,
            ],
            capture_output=True,
            text=True,
            timeout=25,
            env=environment,
        )
        return request.returncode == 0 and request.stdout.strip() == expected_egress
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()


def run_protocol_canary() -> LayerResult:
    if not settings.SPECIAL_MONITOR_L2_ENABLED:
        return LayerResult(layer='l2', ok=True, error_class=None, details={'status': 'disabled'})
    expected_egress = settings.SPECIAL_MONITOR_EXPECTED_EGRESS.strip()
    health_url = urllib.parse.urlsplit(settings.SPECIAL_MONITOR_HEALTH_URL)
    if not expected_egress:
        return LayerResult(
            layer='l2',
            ok=False,
            error_class='not_configured',
            details={'status': 'expected_egress_unset'},
        )
    try:
        ipaddress.ip_address(expected_egress)
    except ValueError:
        return LayerResult(layer='l2', ok=False, error_class='not_configured', details={'status': 'invalid_egress'})
    if health_url.scheme != 'https' or not health_url.hostname or health_url.username or health_url.password:
        return LayerResult(layer='l2', ok=False, error_class='not_configured', details={'status': 'invalid_health_url'})
    xray_path = Path(settings.SPECIAL_MONITOR_XRAY_PATH)
    if not xray_path.is_file() or not os.access(xray_path, os.X_OK):
        return LayerResult(layer='l2', ok=False, error_class='xray_not_configured')
    try:
        user_vpn = UserVPN.objects.select_related('server').get(pk=settings.SPECIAL_MONITOR_CANARY_USER_VPN_ID)
        subscription_url = asyncio.run(get_canary_subscription(user_vpn))
        subscription_link = fetch_subscription_entry(subscription_url, str(user_vpn.vpn_uuid))
        subscription_ok = run_vless(subscription_link, xray_path, expected_egress)
        direct_ok = run_vless(user_vpn.vpn_key, xray_path, expected_egress)
    except Exception:
        return LayerResult(layer='l2', ok=False, error_class='canary_protocol')
    return LayerResult(
        layer='l2',
        ok=subscription_ok and direct_ok,
        error_class=None if subscription_ok and direct_ok else 'canary_protocol',
        details={'subscription_e2e': subscription_ok, 'direct_legacy_e2e': direct_ok},
    )
