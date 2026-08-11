"""Narrow synchronization for the protected internal-inbound canary.

This is deliberately separate from ``MIRROR_INBOUND_IDS``.  It only updates
already-provisioned exact UUID memberships for the one explicitly configured
canary record; it never creates memberships or derives ownership from panel
metadata.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.conf import settings


_EXPECTED_ENDPOINTS = {
    7: 39329,
    9: 46517,
    13: 27914,
    # Inbound 10 is advertised through the public gRPC frontend, never its
    # panel/backend listener.
    10: 80,
}
_ALLOWED = frozenset(_EXPECTED_ENDPOINTS)


class InternalMembershipSyncError(RuntimeError):
    """Fail-closed aggregate result for protected target synchronization."""


@dataclass(frozen=True)
class InternalTarget:
    inbound_id: int


def configured_internal_targets(user_vpn_id: int) -> tuple[InternalTarget, ...]:
    """Return targets only for the fixed, exactly configured canary identity."""
    if not getattr(settings, 'SUBSCRIPTION_INTERNAL_MEMBERSHIP_SYNC_ENABLED', False):
        return ()
    if getattr(settings, 'SUBSCRIPTION_INTERNAL_TEST_USER_IDS', []) != [801] or user_vpn_id != 801:
        return ()
    endpoints = getattr(settings, 'SUBSCRIPTION_INTERNAL_ENDPOINTS', [])
    if not isinstance(endpoints, list) or not endpoints:
        return ()
    ids: list[int] = []
    for endpoint in endpoints:
        if not isinstance(endpoint, dict) or set(endpoint) != {'inbound_id', 'advertised_port', 'label'}:
            return ()
        inbound_id = endpoint.get('inbound_id')
        if (type(inbound_id) is not int or inbound_id not in _ALLOWED or inbound_id in ids
                or type(endpoint.get('advertised_port')) is not int
                or endpoint['advertised_port'] != _EXPECTED_ENDPOINTS[inbound_id]
                or not isinstance(endpoint.get('label'), str) or not endpoint['label'].strip()
                or any(char in endpoint['label'] for char in '\r\n\x00')):
            return ()
        ids.append(inbound_id)
    # The validated canary is all-and-only the retained set.  A partial
    # configuration is unsafe because it silently weakens entitlement parity.
    if set(ids) != _ALLOWED:
        return ()
    return tuple(InternalTarget(inbound_id) for inbound_id in ids)


def _clients(inbound: Any) -> list[Any]:
    clients = getattr(getattr(inbound, 'settings', None), 'clients', None)
    return clients if isinstance(clients, list) else []


async def sync_internal_memberships(api: Any, user_vpn: Any, *, enabled: bool,
                                    expiry_time: int | None = None) -> None:
    """Synchronize existing canary memberships using the caller's logged-in API.

    Each configured target must contain exactly one UUID match.  Reads complete
    before writes, preventing a missing/duplicate target from receiving any
    guessed ownership mutation.  Per-target API failures are aggregated and
    reported without identities or panel details.
    """
    targets = configured_internal_targets(getattr(user_vpn, 'id', None))
    if not targets:
        return
    uuid = str(user_vpn.vpn_uuid)
    found: list[tuple[int, Any]] = []
    errors: list[str] = []
    for target in targets:
        try:
            inbound = await api.inbound.get_by_id(target.inbound_id)
            matches = [client for client in _clients(inbound) if str(getattr(client, 'id', '')) == uuid]
            if len(matches) != 1:
                errors.append(f'{target.inbound_id}:membership')
            else:
                found.append((target.inbound_id, matches[0]))
        except Exception:
            errors.append(f'{target.inbound_id}:read')
    if errors:
        raise InternalMembershipSyncError('internal_membership_sync_failed:' + ','.join(errors))

    for target_id, client in found:
        try:
            client.enable = enabled
            if expiry_time is not None:
                client.expiry_time = expiry_time
            # py3x-ui selects an update route from this field; retain target
            # scope rather than relying on a global UUID/email lookup.
            client.inbound_id = target_id
            await api.client.update(uuid, client)
        except Exception:
            errors.append(f'{target_id}:write')
    if errors:
        raise InternalMembershipSyncError('internal_membership_sync_failed:' + ','.join(errors))
