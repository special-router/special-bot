"""Dial every endpoint a configured provider offers and record whether it answers.

Selection may never dial inside a subscription request: the request carries an
eight-second budget for the whole fetch phase and one Reality handshake costs
seconds. So the verdict is measured here, out of band, and written to
``MirrorEndpointLiveness`` for the renderer to consult.

Deliberately a management command and not a Celery task. The workers run
``--pool=solo``, so a probe that takes minutes would stop every other task for
exactly that long; an operator schedules this from the host instead. Two bounds
apply to one run regardless: ``--concurrency`` limits how many xray processes
exist at once, and ``--max-seconds`` is an absolute wall-clock ceiling after
which no further endpoint is dialled.

The instrument is checked before its results are believed. A run where nothing
at all answered writes no verdict: an xray that will not start, a container
without egress and a provider whose whole fleet died at once are
indistinguishable from here, and only one of them is likely. Marking every
endpoint dead on that evidence would empty a customer's list, which is the one
outcome worse than the blind selection this replaces.
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import unquote

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.subscriptions.models import MirrorEndpointLiveness
from apps.subscriptions.views import (
    _bounded_number,
    _fetch_upstream_payload,
    _parse_upstream_endpoints,
    _valid_upstream_url,
)


logger = logging.getLogger(__name__)

# Plain HTTP through the tunnel, for the same reason the ops probes use it: the
# body is one short line, so "the handshake completed and bytes came back" and
# "something answered with a page" are told apart by length alone.
PROBE_HOST = 'api.ipify.org'
PROBE_PORT = 80
# An endpoint that answers returns an address. Anything longer is a captive
# portal or an error page, which is not this endpoint carrying our traffic.
PROBE_MAX_BODY = 64
# xray binds its inbound and reads its config before it can forward anything.
XRAY_STARTUP_SECONDS = 3.0


class Command(BaseCommand):
    help = 'Probe every mirrored endpoint through xray and record whether it answers.'

    def add_arguments(self, parser):
        parser.add_argument('--concurrency', type=int, default=None,
                            help='Endpoints dialled at once. Defaults to the configured value.')
        parser.add_argument('--max-seconds', type=int, default=None,
                            help='Wall-clock ceiling for the whole run.')
        parser.add_argument('--timeout', type=float, default=None,
                            help='Per-endpoint deadline for one dial.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Probe and report without writing a single verdict.')

    def handle(self, *args, **options):
        xray = getattr(settings, 'SPECIAL_MONITOR_XRAY_PATH', '/usr/local/bin/xray')
        if not os.access(xray, os.X_OK):
            raise CommandError(f'xray is not executable at {xray}; no verdict can be measured')

        concurrency = int(_bounded_number(
            options['concurrency'] if options['concurrency'] is not None
            else getattr(settings, 'SUBSCRIPTION_BACKUP_LIVENESS_PROBE_CONCURRENCY', 4),
            default=4, lower=1, upper=16))
        max_seconds = _bounded_number(
            options['max_seconds'] if options['max_seconds'] is not None
            else getattr(settings, 'SUBSCRIPTION_BACKUP_LIVENESS_PROBE_MAX_SECONDS', 300),
            default=300, lower=10, upper=3600)
        timeout = _bounded_number(
            options['timeout'] if options['timeout'] is not None
            else getattr(settings, 'SUBSCRIPTION_BACKUP_LIVENESS_PROBE_TIMEOUT_SECONDS', 12),
            default=12, lower=1, upper=60)

        targets = self._targets()
        if not targets:
            self.stdout.write('no configured source offered an endpoint to probe')
            return

        deadline = time.monotonic() + max_seconds
        results = self._probe_all(targets, xray, concurrency, deadline, timeout)
        probed = [result for result in results if result is not None]
        alive = [result for result in probed if result[2]]

        for host, port, endpoint_alive, error_class in probed:
            self.stdout.write(f'{"OK  " if endpoint_alive else "DEAD"} {host}:{port} {error_class}')
        self.stdout.write(f'probed={len(probed)}/{len(targets)} alive={len(alive)}')

        if not alive:
            # Every failure shares one cause far more often than every endpoint
            # dies at once, and that cause is usually on this side.
            self.stdout.write('no endpoint answered; writing nothing and leaving selection as it was')
            logger.warning('mirror liveness probe recorded no live endpoint out of %s; '
                           'verdicts withheld', len(probed))
            return
        if options['dry_run']:
            self.stdout.write('dry run: no verdict written')
            return

        now = timezone.now()
        origin = getattr(settings, 'SUBSCRIPTION_BACKUP_LIVENESS_PROBE_ORIGIN', 'bot')
        for host, port, endpoint_alive, error_class in probed:
            MirrorEndpointLiveness.objects.update_or_create(
                host=host, port=port,
                defaults={'alive': endpoint_alive, 'error_class': error_class, 'checked_at': now,
                          'probed_from': origin})
        self.stdout.write(f'wrote {len(probed)} verdicts')

    def _targets(self) -> list[dict]:
        """Every distinct endpoint the configured sources offer, deduplicated.

        The renderer keeps one endpoint per region; the prober needs the ones it
        did not keep, because the whole point is to know which candidate to
        promote when the chosen one is dead. Two outbounds sharing an address
        and a port share a verdict, which is what halves the work on a document
        that lists eight ``BRIDGE_*`` twins of servers it already listed.
        """
        urls = getattr(settings, 'SUBSCRIPTION_BACKUP_UPSTREAM_URLS', [])
        if not isinstance(urls, list):
            return []
        source_limit = int(_bounded_number(
            getattr(settings, 'SUBSCRIPTION_BACKUP_MAX_SOURCES', 8), default=8, lower=1, upper=32))
        targets: dict[tuple[str, int], dict] = {}
        for url in [url for url in urls if isinstance(url, str) and _valid_upstream_url(url)][:source_limit]:
            try:
                headers, payload = _fetch_upstream_payload(url)
                endpoints = _parse_upstream_endpoints(payload, headers)
            except (ValueError, UnicodeError, OSError) as error:
                logger.warning('mirror liveness probe could not read a source: %s',
                               type(error).__name__)
                continue
            for endpoint in endpoints or []:
                targets.setdefault((endpoint['host'], endpoint['port']), endpoint)
        return list(targets.values())

    def _probe_all(self, targets: list[dict], xray: str, concurrency: int,
                   deadline: float, timeout: float) -> list[tuple | None]:
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(self._probe_one, endpoint, xray, deadline, timeout)
                       for endpoint in targets]
            return [future.result() for future in futures]

    def _probe_one(self, endpoint: dict, xray: str, deadline: float,
                   timeout: float) -> tuple[str, int, bool, str] | None:
        """Dial one endpoint, or return None once the run is out of time.

        Declining at the top of the call rather than cancelling in flight is
        what bounds the run: a dial already started still finishes, so the true
        ceiling is the configured one plus a single endpoint's timeout, and no
        xray process is ever left behind by the clock.
        """
        if time.monotonic() >= deadline:
            return None
        host, port = endpoint['host'], endpoint['port']
        local_port = _free_port()
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as handle:
            json.dump(_xray_config(endpoint, local_port), handle)
            path = handle.name
        process = None
        try:
            process = subprocess.Popen([xray, 'run', '-c', path],
                                       stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(XRAY_STARTUP_SECONDS)
            body, error_class = _fetch_through_socks(local_port, timeout)
        except OSError as error:
            body, error_class = '', type(error).__name__
        finally:
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            Path(path).unlink(missing_ok=True)
        alive = bool(body) and len(body) < PROBE_MAX_BODY
        if alive:
            return host, port, True, ''
        return host, port, False, error_class or 'unexpected_body'


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def _xray_config(endpoint: dict, local_port: int) -> dict:
    """Build the smallest client that can prove this endpoint carries traffic.

    Built from the normalized endpoint rather than from a rendered URI so a
    candidate the renderer dropped is still probeable. The uuid is unquoted
    because normalization percent-encodes it for the URI it usually becomes.
    """
    user = {'id': unquote(endpoint['uuid']), 'encryption': 'none'}
    if endpoint['flow']:
        user['flow'] = endpoint['flow']
    stream = {'network': endpoint['network'], 'security': endpoint['security']}
    if endpoint['security'] == 'reality':
        reality = {
            'serverName': endpoint['server_name'],
            'publicKey': endpoint['public_key'],
            'fingerprint': endpoint['fingerprint'] or 'chrome',
            'spiderX': '/',
        }
        if endpoint['short_id']:
            reality['shortId'] = endpoint['short_id']
        stream['realitySettings'] = reality
    elif endpoint['security'] == 'tls':
        stream['tlsSettings'] = {'serverName': endpoint['server_name'],
                                 'fingerprint': endpoint['fingerprint'] or 'chrome'}
    if endpoint['network'] == 'grpc' and endpoint['service_name']:
        stream['grpcSettings'] = {'serviceName': endpoint['service_name']}
    if endpoint['network'] == 'ws' and endpoint['path']:
        stream['wsSettings'] = {'path': endpoint['path']}
    return {
        'log': {'loglevel': 'error'},
        'inbounds': [{'port': local_port, 'listen': '127.0.0.1', 'protocol': 'socks',
                      'settings': {'auth': 'noauth', 'udp': False}}],
        'outbounds': [{
            'protocol': 'vless',
            'settings': {'vnext': [{'address': endpoint['host'], 'port': endpoint['port'],
                                    'users': [user]}]},
            'streamSettings': stream,
        }],
    }


def _fetch_through_socks(local_port: int, timeout: float) -> tuple[str, str]:
    """Return (body, error_class) for one HTTP GET through the local proxy.

    SOCKS5 is spoken by hand rather than through a library because the ops
    probes learned the hard way that urllib silently ignores a proxy and
    reports every endpoint as working. What travels here is our own request or
    nothing.
    """
    try:
        tunnel = socket.create_connection(('127.0.0.1', local_port), timeout=timeout)
    except OSError as error:
        return '', type(error).__name__
    tunnel.settimeout(timeout)
    try:
        tunnel.sendall(b'\x05\x01\x00')
        if tunnel.recv(2) != b'\x05\x00':
            return '', 'socks_greeting'
        tunnel.sendall(b'\x05\x01\x00\x03' + bytes([len(PROBE_HOST)])
                       + PROBE_HOST.encode('ascii') + PROBE_PORT.to_bytes(2, 'big'))
        reply = tunnel.recv(4)
        if len(reply) < 2 or reply[1] != 0:
            return '', 'socks_connect'
        tunnel.recv(6)
        tunnel.sendall(f'GET / HTTP/1.1\r\nHost: {PROBE_HOST}\r\n'
                       'Connection: close\r\n\r\n'.encode('ascii'))
        received = b''
        while len(received) <= PROBE_MAX_BODY * 16:
            chunk = tunnel.recv(4096)
            if not chunk:
                break
            received += chunk
        parts = received.split(b'\r\n\r\n', 1)
        if len(parts) < 2:
            return '', 'no_body'
        return parts[1].decode('utf-8', 'replace').strip(), ''
    except OSError as error:
        return '', type(error).__name__
    finally:
        tunnel.close()
