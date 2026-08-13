"""Dial each subscription endpoint through xray and report whether traffic flows.

Includes our own NL Direct line as a control. An instrument that cannot pass a
known-good endpoint proves nothing about the ones under test, and the first
version of this probe failed everything because urllib silently ignores a SOCKS
proxy — hence the control and the explicit socket wiring below.
"""
import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import django
import socks

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bot.settings')
django.setup()

from django.test import RequestFactory                            # noqa: E402

from apps.subscriptions.views import subscription_proxy           # noqa: E402
from apps.vpn.models import UserVPN                               # noqa: E402

XRAY = os.environ.get('SPECIAL_MONITOR_XRAY_PATH', '/usr/local/bin/xray')
PROBE_HOST, PROBE_PATH = 'api.ipify.org', '/'


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def config_for(link: str, port: int) -> dict:
    url = urlsplit(link)
    query = parse_qs(url.query)
    userinfo, _, hostport = url.netloc.partition('@')
    host, _, server_port = hostport.rpartition(':')
    user = {'id': unquote(userinfo), 'encryption': 'none'}
    if query.get('flow', [''])[0]:
        user['flow'] = query['flow'][0]
    reality = {
        'serverName': query.get('sni', [''])[0],
        'publicKey': query.get('pbk', [''])[0],
        'fingerprint': query.get('fp', ['chrome'])[0],
        'spiderX': query.get('spx', ['/'])[0],
    }
    if query.get('sid', [''])[0]:
        reality['shortId'] = query['sid'][0]
    return {
        'log': {'loglevel': 'error'},
        'inbounds': [{'port': port, 'listen': '127.0.0.1', 'protocol': 'socks',
                      'settings': {'auth': 'noauth', 'udp': False}}],
        'outbounds': [{
            'protocol': 'vless',
            'settings': {'vnext': [{'address': host, 'port': int(server_port), 'users': [user]}]},
            'streamSettings': {
                'network': query.get('type', ['tcp'])[0],
                'security': 'reality',
                'realitySettings': reality,
            },
        }],
    }


def fetch_through(port: int, timeout: float = 12.0) -> str:
    """Plain HTTP GET over the SOCKS5 proxy, without relying on urllib proxies."""
    sock = socks.socksocket()
    sock.set_proxy(socks.SOCKS5, '127.0.0.1', port, rdns=True)
    sock.settimeout(timeout)
    try:
        sock.connect((PROBE_HOST, 80))
        sock.sendall(('GET %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n'
                      % (PROBE_PATH, PROBE_HOST)).encode())
        chunks = []
        while True:
            data = sock.recv(4096)
            if not data:
                break
            chunks.append(data)
        body = b''.join(chunks).split(b'\r\n\r\n', 1)
        return body[1].decode(errors='replace').strip() if len(body) > 1 else 'FAIL:no_body'
    except Exception as error:
        return 'FAIL:%s' % type(error).__name__
    finally:
        sock.close()


def run_one(link: str) -> str:
    port = free_port()
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as handle:
        json.dump(config_for(link, port), handle)
        path = handle.name
    process = subprocess.Popen([XRAY, 'run', '-c', path],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(3)
        return fetch_through(port)
    finally:
        process.terminate()
        process.wait(timeout=5)
        Path(path).unlink(missing_ok=True)


def main() -> int:
    record = UserVPN.objects.get(pk=801)
    response = subscription_proxy(RequestFactory().get('/s'), str(record.sub_id))
    lines = [l for l in base64.b64decode(response.content, validate=True).decode().splitlines() if l.strip()]

    targets = []
    for link in lines:
        label = unquote(link.split('#')[-1])
        if label.startswith('📊'):
            continue
        targets.append((label, link))

    for label, link in targets:
        result = run_one(link)
        ok = not result.startswith('FAIL') and len(result) < 64
        print('%s %-18s %s' % ('OK  ' if ok else 'DEAD', label[:18],
                               (result[:7] + '***') if ok else result))
    return 0


sys.exit(main())
