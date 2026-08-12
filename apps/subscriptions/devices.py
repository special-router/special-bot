"""Per-subscription device binding.

Happ and other Remnawave-compatible clients send an ``x-hwid`` header on every
subscription refresh.  Binding is decided here rather than by the panel: this
deployment's ``limit_ip`` cannot be enforced, because xray sees only the SNI
stream proxy's address and its access log is disabled.

Nothing in this module logs or returns an identifier; callers receive booleans
and durations only.
"""
from __future__ import annotations

import re
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.subscriptions.models import SubscriptionDevice, SubscriptionDeviceReset
from apps.vpn.models import UserVPN


DEFAULT_DEVICE_LIMIT = 2
DEFAULT_RESET_COOLDOWN_HOURS = 24

# The identifier shape published by Happ.  Anything else is treated as no
# identifier at all, so a malformed client cannot occupy a device slot.
_HWID_PATTERN = re.compile(r'[a-zA-Z0-9=-]{10,64}')

# Header metadata is attacker-controlled; each value is capped to its column.
_METADATA_HEADERS = (
    ('device_os', 'x-device-os', 32),
    ('os_version', 'x-ver-os', 32),
    ('device_model', 'x-device-model', 64),
    ('user_agent', 'user-agent', 128),
)


def client_hwid(request) -> str:
    """Return the validated ``x-hwid`` value, or '' when it is unusable."""
    hwid = request.headers.get('x-hwid', '')
    if not isinstance(hwid, str) or not _HWID_PATTERN.fullmatch(hwid):
        return ''
    return hwid


def client_metadata(request) -> dict[str, str]:
    """Collect the optional device description headers, bounded and stripped."""
    metadata = {}
    for field, header, limit in _METADATA_HEADERS:
        value = request.headers.get(header, '')
        metadata[field] = value.strip()[:limit] if isinstance(value, str) else ''
    return metadata


def hwid_strict() -> bool:
    """Whether a request without a usable identifier must be refused."""
    return bool(getattr(settings, 'SUBSCRIPTION_HWID_STRICT', False))


def device_limit_for(user_vpn) -> int:
    """Per-subscription override wins over the global default when set."""
    override = getattr(user_vpn, 'device_limit', None)
    if isinstance(override, int) and not isinstance(override, bool) and override > 0:
        return override
    return _bounded_limit(getattr(settings, 'SUBSCRIPTION_DEVICE_LIMIT', DEFAULT_DEVICE_LIMIT))


def register_device(user_vpn, hwid: str, metadata: dict[str, str]) -> bool:
    """Bind ``hwid`` to the subscription; False once the ceiling is reached.

    The limit is a hard ceiling rather than an LRU window: a flood of forged
    identifiers must be refused outright, never evict a device the customer is
    actually using, and never grow the table past the cap.
    """
    seen = SubscriptionDevice.objects.filter(
        subscription_id=user_vpn.id, hwid=hwid,
    ).update(last_seen_at=timezone.now())
    if seen:
        return True

    limit = device_limit_for(user_vpn)
    try:
        with transaction.atomic():
            # Locking the subscription row serializes concurrent refreshes, so
            # parallel registrations cannot each observe the same free slot.
            UserVPN.objects.select_for_update().filter(pk=user_vpn.id).exists()
            if SubscriptionDevice.objects.filter(subscription_id=user_vpn.id).count() >= limit:
                return False
            SubscriptionDevice.objects.create(subscription_id=user_vpn.id, hwid=hwid, **metadata)
    except IntegrityError:
        # A concurrent refresh from this same device won the insert; still a hit.
        return True
    return True


def reset_devices(user_id: int) -> tuple[bool, timedelta | None]:
    """Clear one user's bound devices, at most once per cooldown.

    Returns ``(True, None)`` when the devices were cleared, or ``(False, wait)``
    with the remaining cooldown when the request came too soon.
    """
    cooldown = timedelta(hours=_bounded_cooldown())
    now = timezone.now()
    with transaction.atomic():
        record, created = SubscriptionDeviceReset.objects.select_for_update().get_or_create(
            telegram_user_id=user_id, defaults={'last_reset_at': now},
        )
        if not created:
            remaining = record.last_reset_at + cooldown - now
            if remaining > timedelta(0):
                return False, remaining
            record.last_reset_at = now
            record.save(update_fields=['last_reset_at'])
        SubscriptionDevice.objects.filter(subscription__user_id=user_id).delete()
    return True, None


def _bounded_limit(value) -> int:
    """A misconfigured limit must not disable binding or uncap the table."""
    try:
        return min(max(int(value), 1), 32)
    except (TypeError, ValueError):
        return DEFAULT_DEVICE_LIMIT


def _bounded_cooldown(value=None) -> int:
    if value is None:
        value = getattr(settings, 'SUBSCRIPTION_DEVICE_RESET_COOLDOWN_HOURS',
                        DEFAULT_RESET_COOLDOWN_HOURS)
    try:
        return min(max(int(value), 1), 24 * 30)
    except (TypeError, ValueError):
        return DEFAULT_RESET_COOLDOWN_HOURS
