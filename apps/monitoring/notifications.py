"""Secret-safe external notification delivery for monitoring transitions."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime

from django.conf import settings

ALLOWED_EVENTS = {'opened', 'recovered'}
ALLOWED_LAYERS = {'l0', 'l1', 'l2', 'host', 'checkout'}


@dataclass(frozen=True)
class NotificationResult:
    delivered: bool
    error_class: str = ''


def build_transition_payload(*, layer: str, event: str, error_class: str, failures: int, created_at: datetime) -> dict[str, object]:
    """Build an aggregate payload containing no client or bearer identifiers."""
    if layer not in ALLOWED_LAYERS or event not in ALLOWED_EVENTS:
        raise ValueError('unsupported_monitor_transition')
    return {
        'service': 'special-bot',
        'layer': layer,
        'event': event,
        'error_class': error_class or 'none',
        'consecutive_failures': int(failures),
        'created_at': created_at.astimezone(UTC).isoformat(),
    }


def _valid_https_url(value: str) -> bool:
    parsed = urllib.parse.urlsplit(value)
    return bool(parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password)


def send_transition_notification(payload: dict[str, object]) -> NotificationResult:
    """POST one JSON transition to an optional HTTPS webhook.

    Disabled or unconfigured paging is a no-op. Errors are reduced to classes;
    response bodies and webhook URLs are never returned or logged.
    """
    if not settings.SPECIAL_MONITOR_PAGING_ENABLED:
        return NotificationResult(delivered=False, error_class='disabled')
    url = settings.SPECIAL_MONITOR_PAGING_WEBHOOK_URL.strip()
    owner = settings.SPECIAL_MONITOR_PAGING_OWNER.strip()
    if not owner or not _valid_https_url(url):
        return NotificationResult(delivered=False, error_class='not_configured')
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, separators=(',', ':')).encode(),
        headers={'Content-Type': 'application/json', 'User-Agent': 'SPECIAL-monitor/1'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.SPECIAL_MONITOR_PAGING_TIMEOUT) as response:
            if 200 <= response.status < 300:
                return NotificationResult(delivered=True)
            return NotificationResult(delivered=False, error_class='http_status')
    except Exception as error:
        return NotificationResult(delivered=False, error_class=type(error).__name__)
