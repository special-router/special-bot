#!/usr/bin/env python3
"""Memory-only LKG subscription edge for the existing RU delivery host.

Cache keys are SHA-256 digests; bearer paths, response bodies, UUIDs and device
identifiers are never written to disk or logs. Only successful configuration
documents are cached. Fresh entries avoid the BOT hop; stale entries are used
only when the origin is unhealthy.
"""
from __future__ import annotations

import hashlib
import http.client
import os
import re
import secrets
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


ORIGIN_HOST = os.environ.get('SPECIAL_CONFIG_ORIGIN_HOST', '72.56.23.226')
ORIGIN_PORT = int(os.environ.get('SPECIAL_CONFIG_ORIGIN_PORT', '8001'))
FRESH_SECONDS = 900
STALE_SECONDS = 86400
MAX_BODY = 512 * 1024
MAX_ENTRIES = 10000
PATH_RE = re.compile(r'^/sub/[A-Za-z0-9_-]{8,96}$')
CACHE: OrderedDict[str, tuple[float, int, dict[str, str], bytes]] = OrderedDict()
LOCK = threading.RLock()
FORWARD = ('user-agent', 'accept', 'accept-encoding', 'x-hwid', 'x-device-os',
           'x-ver-os', 'x-device-model', 'x-request-id')
KEY_HEADERS = tuple(name for name in FORWARD if name != 'x-request-id')
RETURN = ('content-type', 'content-encoding', 'profile-update-interval',
          'subscription-userinfo', 'routing-enable', 'x-hwid-active',
          'x-hwid-limit', 'x-hwid-max-devices-reached', 'x-hwid-not-supported',
          'x-hwid-status', 'profile-title', 'support-url',
          'profile-web-page-url', 'announce', 'cache-control', 'pragma',
          'x-request-id')


def cache_key(handler: BaseHTTPRequestHandler) -> str:
    fields = [handler.path, *(handler.headers.get(name, '') for name in KEY_HEADERS)]
    return hashlib.sha256('\0'.join(fields).encode('utf-8')).hexdigest()


def fetch(handler: BaseHTTPRequestHandler) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection(ORIGIN_HOST, ORIGIN_PORT, timeout=8)
    headers = {name: handler.headers[name] for name in FORWARD if handler.headers.get(name)}
    headers['Host'] = 'special-wifi.link'
    try:
        connection.request('GET', handler.path, headers=headers)
        response = connection.getresponse()
        body = response.read(MAX_BODY + 1)
        if len(body) > MAX_BODY:
            raise OSError('origin_body_too_large')
        selected = {name.casefold(): value for name, value in response.getheaders()
                    if name.casefold() in RETURN}
        return response.status, selected, body
    finally:
        connection.close()


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_args):
        return

    def do_GET(self):
        if self.path == '/_health':
            return self.send(200, {'content-type': 'text/plain'}, b'ok\n', 'health')
        if not PATH_RE.fullmatch(self.path):
            return self.send(404, {'content-type': 'text/plain'}, b'', 'miss')
        key = cache_key(self)
        now = time.monotonic()
        with LOCK:
            cached = CACHE.get(key)
            if cached and now - cached[0] <= FRESH_SECONDS:
                CACHE.move_to_end(key)
                return self.send(cached[1], cached[2], cached[3], 'fresh')
        try:
            status, headers, body = fetch(self)
            if status == 200 and body:
                with LOCK:
                    CACHE[key] = (now, status, headers, body)
                    CACHE.move_to_end(key)
                    while len(CACHE) > MAX_ENTRIES:
                        CACHE.popitem(last=False)
            return self.send(status, headers, body, 'origin')
        except (OSError, TimeoutError, http.client.HTTPException):
            if cached and now - cached[0] <= STALE_SECONDS:
                return self.send(cached[1], cached[2], cached[3], 'stale')
            return self.send(502, {'content-type': 'text/plain'}, b'', 'unavailable')

    def send(self, status: int, headers: dict[str, str], body: bytes, edge: str):
        self.send_response(status)
        for name, value in headers.items():
            if name != 'x-request-id':
                self.send_header(name, value)
        request_id = self.headers.get('X-Request-ID', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', request_id):
            request_id = headers.get('x-request-id', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', request_id):
            request_id = secrets.token_hex(16)
        self.send_header('X-Request-ID', request_id)
        self.send_header('X-Config-Edge', edge)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)


if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', 18081), Handler).serve_forever()
