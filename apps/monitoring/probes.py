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
from django.utils import timezone
from telegram import Bot, LabeledPrice

from apps.analytics.models import MoneyEvent
from apps.servers.control_plane import fetch_control_plane_client_ids, fetch_inbound_snapshots
from apps.servers.models import Server, TariffServer
from apps.servers.remnawave import RemnawaveAPI
from apps.servers.remnawave_client import panel_identity
from apps.servers.subscription_connector import build_subscription_url
from apps.vpn.management.commands.audit_legacy_vpn import get_server_entitlement
from apps.vpn.models import UserVPN


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


def _read_meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    with open('/proc/meminfo', encoding='utf-8') as handle:
        for line in handle:
            key, raw = line.split(':', 1)
            values[key] = int(raw.strip().split()[0])
    return values


def _read_oom_kill_count() -> int:
    with open('/proc/vmstat', encoding='utf-8') as handle:
        for line in handle:
            key, raw = line.split()
            if key == 'oom_kill':
                return int(raw)
    return 0


def run_host_capacity_probe() -> LayerResult:
    try:
        meminfo = _read_meminfo()
        available_mb = meminfo['MemAvailable'] // 1024
        swap_total_mb = meminfo.get('SwapTotal', 0) // 1024
        swap_free_mb = meminfo.get('SwapFree', 0) // 1024
        swap_used_mb = max(0, swap_total_mb - swap_free_mb)
        load1 = os.getloadavg()[0]
        cpus = os.cpu_count() or 1
        oom_kills = _read_oom_kill_count()
    except (OSError, KeyError, ValueError):
        return LayerResult(layer='host', ok=False, error_class='host_metrics')

    reasons = []
    if available_mb < settings.SPECIAL_MONITOR_MIN_AVAILABLE_MB:
        reasons.append('memory_low')
    if swap_total_mb < settings.SPECIAL_MONITOR_MIN_SWAP_MB:
        reasons.append('swap_missing')
    if load1 > cpus * settings.SPECIAL_MONITOR_MAX_LOAD_PER_CPU:
        reasons.append('load_high')
    if oom_kills > settings.SPECIAL_MONITOR_MAX_OOM_KILLS:
        reasons.append('oom_kill')
    return LayerResult(
        layer='host',
        ok=not reasons,
        error_class=reasons[0] if reasons else None,
        immediate='oom_kill' in reasons or 'swap_missing' in reasons,
        details={
            'mem_available_mb': available_mb,
            'swap_total_mb': swap_total_mb,
            'swap_used_mb': swap_used_mb,
            'load1_per_cpu': round(load1 / cpus, 2),
            'oom_kills': oom_kills,
        },
    )


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


async def _canary_subscription_from_xui(user_vpn: UserVPN) -> str:
    from utils.py3xui.async_api import AsyncApi

    api = AsyncApi(user_vpn.server.vpn_url, user_vpn.server.vpn_username,
                   user_vpn.server.vpn_password)
    await api.login()
    inbound = await api.inbound.get_by_id(user_vpn.server.inbound_id)
    matches = [client for client in inbound.settings.clients
               if str(client.id) == str(user_vpn.vpn_uuid)]
    if len(matches) != 1 or not matches[0].sub_id:
        raise RuntimeError('canary_client_missing')
    return build_subscription_url(settings.SUBSCRIPTION_BASE_URL, matches[0].sub_id)


async def _canary_subscription_from_remnawave(user_vpn: UserVPN) -> str:
    # Имя строится из ленивой связи ``user``; ``panel_identity`` берёт её так,
    # чтобы async-контекст не превратил это в SynchronousOnlyOperation, которое
    # проба показала бы как аварию туннеля.
    username, _ = await panel_identity(user_vpn)
    panel_user = await RemnawaveAPI().get_user_by_username(username)
    if panel_user is None or str(panel_user.get('status')) != 'ACTIVE':
        raise RuntimeError('canary_client_missing')
    if str(panel_user.get('vlessUuid') or '') != str(user_vpn.vpn_uuid):
        raise RuntimeError('canary_client_missing')
    sub_id = str(panel_user.get('shortUuid') or '')
    if not sub_id:
        raise RuntimeError('canary_client_missing')
    return build_subscription_url(settings.SUBSCRIPTION_BASE_URL, sub_id)


async def get_canary_subscription(user_vpn: UserVPN) -> str:
    """Ссылка подписки канарейки, как её увидит клиент.

    Проверяется не наша запись в базе, а то, что действующий control plane
    знает этого клиента и держит его включённым: канарейка ловит ровно случай
    «в базе доступ есть, в панели нет». Источник тот же, что и у выдачи, иначе
    после отката флага она проверяла бы уже не ту панель.
    """
    if getattr(settings, 'REMNAWAVE_ENABLED', False):
        return await _canary_subscription_from_remnawave(user_vpn)
    return await _canary_subscription_from_xui(user_vpn)


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, msg, headers, new_url):
        raise RuntimeError('subscription_redirect')


_no_redirect_opener = urllib.request.build_opener(NoRedirectHandler)


def fetch_subscription_entry(url: str, expected_uuid: str, *, excluded: str = '',
                             excluded_hosts: set[str] | None = None) -> str:
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
        if link == excluded or not link.startswith('vless://'):
            continue
        parsed = urllib.parse.urlsplit(link)
        if urllib.parse.unquote(parsed.username or '') != expected_uuid:
            continue
        host = (parsed.hostname or '').lower()
        if host in {'127.0.0.1', 'localhost', '::1'} or host in (excluded_hosts or set()):
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
        # Reality anti-replay can cause flakes on low-latency paths; retry up to 3 times.
        subscription_ok = any(run_vless(subscription_link, xray_path, expected_egress) for _ in range(3))
        # The historical ``vpn_key`` is the RU relay link for every record. That
        # path is intentionally no longer mandatory, so using it as the second
        # half of L2 kept the whole product red after the CDN split. Verify a
        # different current VLESS line from the same rendered subscription
        # instead: this still proves two real data-plane paths without pinning
        # monitoring to a retired legacy field.
        secondary_link = fetch_subscription_entry(
            subscription_url, str(user_vpn.vpn_uuid), excluded=subscription_link,
            excluded_hosts={urllib.parse.urlsplit(user_vpn.vpn_key).hostname or ''})
        direct_ok = any(run_vless(secondary_link, xray_path, expected_egress) for _ in range(3))
    except Exception:
        return LayerResult(layer='l2', ok=False, error_class='canary_protocol')
    return LayerResult(
        layer='l2',
        ok=subscription_ok and direct_ok,
        error_class=None if subscription_ok and direct_ok else 'canary_protocol',
        details={'subscription_e2e': subscription_ok, 'direct_legacy_e2e': direct_ok},
    )


def cash_gap_days() -> int | None:
    """Whole days since the last rouble actually received.

    ``None`` means no cash has ever been recorded. That is deliberately not a
    gap: on an unseeded database it is indistinguishable from a silent one, and
    alerting on it would page on every fresh deployment instead of on a real
    stall.
    """
    latest = (
        MoneyEvent.objects.filter(cash_amount__gt=0)
        .order_by('-occurred_at')
        .values_list('occurred_at', flat=True)
        .first()
    )
    if latest is None:
        return None
    return max(0, (timezone.now() - latest).days)


async def create_probe_invoice_link(timeout: float) -> None:
    """Ask Bot API for one invoice link and drop it.

    A link bills nobody until someone opens and pays it, which is what makes
    the live provider token safe to exercise on a schedule. The returned link
    is discarded rather than stored: it is payable, so it does not belong in
    monitoring state.
    """
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    try:
        # Inside the ``try``, because ``initialize`` marks the request objects
        # initialised before calling ``get_me`` — and ``get_me`` is exactly what
        # a revoked bot token fails. Left outside, the most likely failure of
        # all leaks two live HTTPXRequest clients bound to a loop we then close.
        await bot.initialize()
        await bot.create_invoice_link(
            title='Проверка оплаты',
            description='Служебная проверка платёжного пути. Оплачивать не нужно.',
            payload='special-monitor-checkout',
            provider_token=settings.YOUMONEY_TOKEN,
            currency='RUB',
            prices=[LabeledPrice('Проверка', settings.SPECIAL_MONITOR_CHECKOUT_AMOUNT)],
            read_timeout=timeout,
            write_timeout=timeout,
            connect_timeout=timeout,
            pool_timeout=timeout,
        )
    finally:
        await bot.shutdown()


# Bot API returns a fixed identifier for each payment rejection, and it is the
# only thing separating "the token is wrong" from "Telegram had a bad minute" —
# both arrive as ``BadRequest``. The identifier is matched, never echoed: the
# rest of the message is Telegram's to change, and a future one could carry
# something that must not reach a log or a page.
INVOICE_REJECTIONS = (
    ('PAYMENT_PROVIDER_INVALID', 'provider_token_rejected'),
    ('CURRENCY_TOTAL_AMOUNT_INVALID', 'invoice_amount_rejected'),
    ('CURRENCY_INVALID', 'invoice_currency_rejected'),
)


def classify_invoice_error(error: Exception) -> str:
    """Name the failure an operator has to act on, in this module's own words.

    An unrecognised failure keeps the coarse exception class, which is still
    the useful split: ``InvalidToken`` is the bot token, ``NetworkError`` and
    ``TimedOut`` are Telegram, and neither is the provider.
    """
    # SECRET. On ``InvalidToken`` this string is
    # "The token `<bot token>` was rejected by the server." — the live bot token
    # in full. It exists to be matched against and nothing else: never log it,
    # never return it, never put it in ``details`` or a notification payload.
    # It reads like an ordinary error message, which is the whole danger.
    description = str(error).upper()
    for identifier, error_class in INVOICE_REJECTIONS:
        if identifier in description:
            return error_class
    return type(error).__name__[:64]


def probe_invoice_link(timeout: float) -> str | None:
    """Return a coarse failure class for the invoice call, or ``None`` if it worked.

    The per-phase httpx timeouts above bound connect, read and write
    separately, so a call that stalls between phases can outlive all of them;
    the outer deadline is what actually hands the worker back. Workers run
    ``--pool=solo``, where a wedged task takes the whole queue with it.
    """
    if not settings.TELEGRAM_BOT_TOKEN or not settings.YOUMONEY_TOKEN:
        return 'not_configured'
    try:
        asyncio.run(asyncio.wait_for(create_probe_invoice_link(timeout), timeout * 2))
    except TimeoutError:
        return 'invoice_timeout'
    except Exception as error:
        return classify_invoice_error(error)
    return None


def probe_tariff_lookup() -> str | None:
    """Resolve the tariff the way the top-up handler resolves it.

    ``top_up_balance_days`` reaches ``send_invoice`` only after a bare
    ``TariffServer.objects.aget()``, and nothing constrains that table to one
    row. Empty or doubled, the customer taps an amount and gets an exception
    instead of an invoice — while the provider itself is perfectly healthy. The
    lookup is repeated here rather than inferred, because a probe that skips it
    stays green through exactly that outage. ``aget`` is ``get`` behind
    ``sync_to_async``, so the sync call resolves identically.
    """
    try:
        TariffServer.objects.get()
    except TariffServer.DoesNotExist:
        return 'tariff_missing'
    except TariffServer.MultipleObjectsReturned:
        return 'tariff_ambiguous'
    except Exception as error:
        return type(error).__name__[:64]
    return None


def run_checkout_probe() -> LayerResult:
    """Whether a customer could pay right now, and whether anyone has been paying.

    Three questions, kept apart on purpose, and every verdict survives into
    ``details`` even when another one outranks it. Order follows the customer's
    own path: our tariff lookup runs before the provider is ever asked, so a
    broken lookup is reported as ours rather than blamed on the provider. A
    failing checkout in turn outranks the gap, because a zero gap alongside a
    broken checkout only means the last payment landed before the break.
    """
    tariff_error = probe_tariff_lookup()
    invoice_error = probe_invoice_link(float(settings.SPECIAL_MONITOR_CHECKOUT_TIMEOUT))
    gap_days = cash_gap_days()
    threshold = settings.SPECIAL_MONITOR_CASH_GAP_DAYS
    gap_breached = gap_days is not None and gap_days > threshold
    return LayerResult(
        layer='checkout',
        ok=tariff_error is None and invoice_error is None and not gap_breached,
        error_class=tariff_error or invoice_error or ('cash_gap' if gap_breached else None),
        details={
            'tariff_ok': tariff_error is None,
            'tariff_error_class': tariff_error,
            'invoice_ok': invoice_error is None,
            'invoice_error_class': invoice_error,
            'cash_gap_days': gap_days,
            'cash_gap_threshold': threshold,
        },
    )
