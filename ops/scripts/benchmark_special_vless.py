#!/usr/bin/env python3
"""Benchmark existing SPECIAL canary VLESS endpoints without exposing secrets."""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
import urllib.parse
import urllib.request

SCRIPT_PATH = Path(__file__).resolve()
REPOSITORY_ROOT = SCRIPT_PATH.parents[2] if len(SCRIPT_PATH.parents) > 2 else Path.cwd()
if not (REPOSITORY_ROOT / 'manage.py').is_file() and (Path.cwd() / 'manage.py').is_file():
    REPOSITORY_ROOT = Path.cwd()
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bot.settings')

import django

django.setup()

from django.conf import settings

from apps.monitoring.probes import build_xray_config, child_environment, free_port, get_canary_subscription, wait_port
from apps.vpn.models import UserVPN


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, msg, headers, new_url):
        raise RuntimeError('subscription_redirect')


def subscription_links(url: str, expected_uuid: str) -> list[str]:
    request = urllib.request.Request(url, headers={'User-Agent': 'SPECIAL-benchmark/1'})
    opener = urllib.request.build_opener(NoRedirectHandler)
    with opener.open(request, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError('subscription_http')
        payload = response.read(1024 * 1024)
    decoded = base64.b64decode(b''.join(payload.split()), validate=True).decode('utf-8')
    links = []
    for line in decoded.splitlines():
        link = line.strip()
        if not link.startswith('vless://'):
            continue
        parsed = urllib.parse.urlsplit(link)
        if urllib.parse.unquote(parsed.username or '') != expected_uuid:
            continue
        if (parsed.hostname or '').lower() in {'127.0.0.1', 'localhost', '::1'}:
            continue
        links.append(link)
    if len(links) < 2:
        raise RuntimeError('benchmark_endpoints_missing')
    return links


def label_for(link: str, index: int) -> str:
    fragment = urllib.parse.unquote(urllib.parse.urlsplit(link).fragment).lower()
    words = {word for word in fragment.replace('-', ' ').replace('_', ' ').split() if word}
    if 'direct' in words:
        return 'direct'
    if 'relay' in words:
        return 'relay'
    return f'endpoint_{index}'


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * fraction + 0.999999)))
    return ordered[rank]


def benchmark_link(link: str, urls: list[str], attempts: int, max_time: int) -> dict[str, object]:
    port = free_port()
    environment = child_environment()
    xray_path = Path(settings.SPECIAL_MONITOR_XRAY_PATH)
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
    samples: list[dict[str, object]] = []
    try:
        if not wait_port(process, port):
            raise RuntimeError('xray_start')
        for attempt in range(attempts):
            url = urls[attempt % len(urls)]
            command = [
                'curl', '-fsS', '-o', '/dev/null', '--socks5-hostname', f'127.0.0.1:{port}',
                '--connect-timeout', '10', '--max-time', str(max_time),
                '-w', '%{http_code} %{time_connect} %{time_appconnect} %{time_starttransfer} %{time_total} %{size_download} %{speed_download}',
                url,
            ]
            started = time.monotonic()
            result = subprocess.run(command, capture_output=True, text=True, timeout=max_time + 5, env=environment)
            elapsed = time.monotonic() - started
            fields = result.stdout.strip().split()
            sample: dict[str, object] = {
                'ok': False,
                'elapsed_s': round(elapsed, 3),
                'error': 'curl' if result.returncode else 'http_or_parse',
            }
            if result.returncode == 0 and len(fields) == 7 and fields[0].startswith(('2', '3')):
                sample.update(
                    ok=True,
                    error=None,
                    connect_ms=round(float(fields[1]) * 1000, 2),
                    tls_ms=round(float(fields[2]) * 1000, 2),
                    ttfb_ms=round(float(fields[3]) * 1000, 2),
                    total_ms=round(float(fields[4]) * 1000, 2),
                    bytes=int(float(fields[5])),
                    mbps=round(float(fields[6]) * 8 / 1_000_000, 3),
                )
            samples.append(sample)
            time.sleep(0.5)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
    good = [sample for sample in samples if sample['ok']]
    totals = [float(sample['total_ms']) for sample in good]
    speeds = [float(sample['mbps']) for sample in good]
    return {
        'attempts': attempts,
        'successes': len(good),
        'failures': attempts - len(good),
        'success_rate': round(len(good) / attempts, 4),
        'median_total_ms': round(statistics.median(totals), 2) if totals else None,
        'p95_total_ms': round(percentile(totals, 0.95), 2) if totals else None,
        'median_mbps': round(statistics.median(speeds), 3) if speeds else None,
        'min_mbps': round(min(speeds), 3) if speeds else None,
        'max_mbps': round(max(speeds), 3) if speeds else None,
        'samples': samples,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', action='append', required=True, help='HTTPS benchmark URL; repeat for multiple fixed files')
    parser.add_argument('--attempts', type=int, default=5)
    parser.add_argument('--max-time', type=int, default=60)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 1 <= args.attempts <= 50:
        raise SystemExit('--attempts must be between 1 and 50')
    urls = []
    for raw in args.url:
        parsed = urllib.parse.urlsplit(raw)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise SystemExit('benchmark URLs must be credential-free HTTPS URLs')
        urls.append(raw)
    user_vpn = UserVPN.objects.select_related('server').get(pk=settings.SPECIAL_MONITOR_CANARY_USER_VPN_ID)
    subscription_url = asyncio.run(get_canary_subscription(user_vpn))
    links = subscription_links(subscription_url, str(user_vpn.vpn_uuid))
    report = {'schema': 1, 'attempts_per_endpoint': args.attempts, 'endpoints': {}}
    for index, link in enumerate(links, 1):
        report['endpoints'][label_for(link, index)] = benchmark_link(link, urls, args.attempts, args.max_time)
    print(json.dumps(report, sort_keys=True))
    return 0 if all(item['failures'] == 0 for item in report['endpoints'].values()) else 1


if __name__ == '__main__':
    sys.exit(main())
