from __future__ import annotations

import logging
import re
import secrets
import time


logger = logging.getLogger('config_delivery')
_REQUEST_ID_RE = re.compile(r'^[A-Za-z0-9_-]{16,64}$')


class ConfigDeliveryObservabilityMiddleware:
    """Attach a safe correlation id and log delivery stage without bearer data."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        supplied = request.headers.get('X-Request-ID', '')
        request_id = supplied if _REQUEST_ID_RE.fullmatch(supplied) else secrets.token_hex(16)
        request.config_request_id = request_id
        started = time.monotonic()
        response = self.get_response(request)
        response['X-Request-ID'] = request_id
        if request.path.startswith('/sub/') or request.path.startswith('/api/v1/vpn/router/'):
            user_agent = request.headers.get('User-Agent', '').casefold()
            client = 'happ' if 'happ' in user_agent else 'v2rayng' if 'v2rayng' in user_agent else 'other'
            logger.info(
                'config_delivery request_id=%s method=%s client=%s status=%s bytes=%s '
                'encoding=%s elapsed_ms=%s',
                request_id,
                request.method,
                client,
                response.status_code,
                len(getattr(response, 'content', b'')),
                response.get('Content-Encoding', 'identity'),
                round((time.monotonic() - started) * 1000),
            )
        return response
