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

from apps.subscriptions.models import (
    SubscriptionDevice,
    SubscriptionDeviceBindingWindow,
    SubscriptionDeviceRegistrationRate,
    SubscriptionDeviceReset,
)
from apps.vpn.models import UserVPN


DEFAULT_DEVICE_LIMIT = 2
# The reset is an authenticated action and no longer the only way out of a
# lockout, so it need not be rationed by the day.
DEFAULT_RESET_COOLDOWN_HOURS = 1
DEFAULT_BINDING_WINDOW_MINUTES = 15
DEFAULT_REGISTRATION_LIMIT = 5
REGISTRATION_PERIOD = timedelta(hours=1)

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


def binding_window() -> timedelta:
    """How long one 'привязать устройство' request keeps registration open."""
    return timedelta(minutes=_bounded_window_minutes())


def open_binding_window(user_id: int) -> timedelta:
    """Record the account holder's consent to bind a device, and for how long.

    Only the Telegram side calls this, because only there is the requester's
    identity authenticated.  Re-opening simply restarts the window.
    """
    SubscriptionDeviceBindingWindow.objects.update_or_create(
        telegram_user_id=user_id, defaults={'opened_at': timezone.now()},
    )
    return binding_window()


def binding_window_open(user_id: int) -> bool:
    """Whether that user asked for a new device recently enough."""
    return SubscriptionDeviceBindingWindow.objects.filter(
        telegram_user_id=user_id, opened_at__gt=timezone.now() - binding_window(),
    ).exists()


def binding_window_required() -> bool:
    """Whether a *new* identifier needs the account holder's open window.

    False is the launch state only: on the first deploy no subscription has any
    bound device, so requiring a window immediately would refuse the second
    device of every existing customer before anyone knows a window exists.
    """
    return bool(getattr(settings, 'SUBSCRIPTION_DEVICE_BINDING_WINDOW_REQUIRED', True))


def register_device(user_vpn, hwid: str, metadata: dict[str, str]) -> bool:
    """Serve ``hwid``; bind it first when this subscription may take a new one.

    A device already bound is always served, so nobody can be pushed off their
    own subscription.  Binding an *unknown* identifier needs more than a free
    slot, because ``/sub/<sub_id>`` is unauthenticated and anyone holding a
    leaked sub_id could otherwise spend the customer's slots on invented
    identifiers.  It needs, in order: room under the limit, the account holder
    having opened a binding window from the bot, and registration budget left
    for the period.  The limit stays a hard ceiling rather than an LRU window —
    an attacker polls far more often than a real client, so evicting the least
    recent device would evict the customer.

    The one exception is a subscription with no devices at all: the first
    identifier binds unattended, so a fresh purchase just works.  During the
    rollout ``SUBSCRIPTION_DEVICE_BINDING_WINDOW_REQUIRED=false`` drops the
    window step for every device; the limit and the budget still apply.
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
            bound = SubscriptionDevice.objects.filter(subscription_id=user_vpn.id).count()
            if bound >= limit:
                return False
            if bound and binding_window_required() and not binding_window_open(user_vpn.user_id):
                return False
            if not _spend_registration_budget(user_vpn.id):
                return False
            SubscriptionDevice.objects.create(subscription_id=user_vpn.id, hwid=hwid, **metadata)
    except IntegrityError:
        # A concurrent refresh from this same device won the insert; still a hit.
        return True
    return True


def reset_devices(user_id: int) -> tuple[bool, timedelta | None]:
    """Clear one user's bound devices and open their binding window.

    Returns ``(True, None)`` when the devices were cleared, or ``(False, wait)``
    with the remaining cooldown when the request came too soon.  Clearing alone
    would leave the user unable to re-bind, so the two go together.
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
        open_binding_window(user_id)
    return True, None


def _spend_registration_budget(subscription_id: int) -> bool:
    """Consume one registration from this subscription's rolling allowance.

    Freeing slots with a reset must not hand out a fresh flooding budget, so the
    counter is keyed to the subscription and outlives the rows it counted.
    """
    limit = _bounded_registrations()
    now = timezone.now()
    record, created = SubscriptionDeviceRegistrationRate.objects.select_for_update().get_or_create(
        subscription_id=subscription_id, defaults={'period_started_at': now, 'registrations': 1},
    )
    if created:
        return True
    if now - record.period_started_at >= REGISTRATION_PERIOD:
        record.period_started_at = now
        record.registrations = 1
    elif record.registrations >= limit:
        return False
    else:
        record.registrations += 1
    record.save(update_fields=['period_started_at', 'registrations'])
    return True


def _bounded_limit(value) -> int:
    """A misconfigured limit must not disable binding or uncap the table."""
    try:
        return min(max(int(value), 1), 32)
    except (TypeError, ValueError):
        return DEFAULT_DEVICE_LIMIT


def _bounded_window_minutes() -> int:
    """A misconfigured window must never become permanently open."""
    value = getattr(settings, 'SUBSCRIPTION_DEVICE_BINDING_WINDOW_MINUTES',
                    DEFAULT_BINDING_WINDOW_MINUTES)
    try:
        return min(max(int(value), 1), 24 * 60)
    except (TypeError, ValueError):
        return DEFAULT_BINDING_WINDOW_MINUTES


def _bounded_registrations() -> int:
    value = getattr(settings, 'SUBSCRIPTION_DEVICE_REGISTRATIONS_PER_HOUR',
                    DEFAULT_REGISTRATION_LIMIT)
    try:
        return min(max(int(value), 1), 64)
    except (TypeError, ValueError):
        return DEFAULT_REGISTRATION_LIMIT


def _bounded_cooldown(value=None) -> int:
    if value is None:
        value = getattr(settings, 'SUBSCRIPTION_DEVICE_RESET_COOLDOWN_HOURS',
                        DEFAULT_RESET_COOLDOWN_HOURS)
    try:
        return min(max(int(value), 1), 24 * 30)
    except (TypeError, ValueError):
        return DEFAULT_RESET_COOLDOWN_HOURS
