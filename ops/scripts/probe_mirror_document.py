"""Probe every server in the provider document, not only the nine we render.

The question is which of them are bridges: an entry in one country and an exit
in another. Tag text hints at it; only dialling proves it.
"""
import json, os, re, socket, subprocess, tempfile, time, urllib.request
from pathlib import Path

XRAY = '/usr/local/bin/xray'
HOST, PORT = 'api.ipify.org', 80

url = json.load(open('/run/secrets/subscription-backup.json'))['upstream_urls'][0]
env = dict(re.findall(r'^([A-Z_]+)=(.*)$', open('/app/.env-probe').read(), re.M)) if os.path.exists('/app/.env-probe') else {}
from django.conf import settings
h = {'User-Agent': settings.SUBSCRIPTION_BACKUP_UPSTREAM_USER_AGENT or 'SFI/1.9',
     'x-hwid': settings.SUBSCRIPTION_BACKUP_UPSTREAM_HWID,
     'x-device-os': settings.SUBSCRIPTION_BACKUP_UPSTREAM_DEVICE_OS or 'Android'}
doc = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=20).read())


def geo(ip):
    try:
        with urllib.request.urlopen('https://ipinfo.io/%s/json' % ip, timeout=8) as r:
            d = json.load(r)
            return d.get('country', '??'), (d.get('org') or '')[:24]
    except Exception:
        return '??', ''


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def socks5_get(port, timeout=12.0):
    try:
        s = socket.create_connection(('127.0.0.1', port), timeout=timeout)
    except Exception:
        return 'FAIL'
    s.settimeout(timeout)
    try:
        s.sendall(b'\x05\x01\x00')
        if s.recv(2) != b'\x05\x00':
            return 'FAIL'
        s.sendall(b'\x05\x01\x00\x03' + bytes([len(HOST)]) + HOST.encode() + PORT.to_bytes(2, 'big'))
        rep = s.recv(4)
        if len(rep) < 2 or rep[1] != 0:
            return 'FAIL'
        s.recv(6)
        s.sendall(('GET / HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n' % HOST).encode())
        buf = b''
        while True:
            d = s.recv(4096)
            if not d:
                break
            buf += d
        p = buf.split(b'\r\n\r\n', 1)
        return p[1].decode(errors='replace').strip() if len(p) > 1 else 'FAIL'
    except Exception:
        return 'FAIL'
    finally:
        s.close()


rows = []
for o in doc['outbounds']:
    if o.get('type') != 'vless' or o.get('server') == '1.1.1.1':
        continue
    tag = o.get('tag', '')
    tls = o.get('tls') or {}
    reality = tls.get('reality') or {}
    port = free_port()
    cfg = {'log': {'loglevel': 'error'},
           'inbounds': [{'port': port, 'listen': '127.0.0.1', 'protocol': 'socks',
                         'settings': {'auth': 'noauth', 'udp': False}}],
           'outbounds': [{'protocol': 'vless',
                          'settings': {'vnext': [{'address': o['server'], 'port': o['server_port'],
                                                  'users': [{'id': o['uuid'], 'encryption': 'none',
                                                             **({'flow': o['flow']} if o.get('flow') else {})}]}]},
                          'streamSettings': {'network': 'tcp', 'security': 'reality',
                                             'realitySettings': {'serverName': tls.get('server_name', ''),
                                                                 'publicKey': reality.get('public_key', ''),
                                                                 'fingerprint': tls.get('utls', {}).get('fingerprint', 'chrome'),
                                                                 **({'shortId': reality['short_id']} if reality.get('short_id') else {})}}}]}
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as fh:
        json.dump(cfg, fh)
        path = fh.name
    p = subprocess.Popen([XRAY, 'run', '-c', path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(3)
        out = socks5_get(port)
    finally:
        p.terminate()
        p.wait(timeout=5)
        Path(path).unlink(missing_ok=True)
    ec, eorg = geo(o['server'])
    if out == 'FAIL' or len(out) > 40:
        rows.append((tag, o['server'], ec, '-', '-', 'DEAD', eorg))
    else:
        xc, _ = geo(out)
        rows.append((tag, o['server'], ec, out, xc, 'BRIDGE %s->%s' % (ec, xc) if ec != xc else 'direct', eorg))

print('%-30s %-17s %-3s %-16s %-3s %s' % ('tag', 'entry', 'in', 'exit', 'out', 'verdict'))
for t, s, ec, x, xc, v, org in rows:
    print('%-30s %-17s %-3s %-16s %-3s %s' % (t[:30], s, ec, x, xc, v))
