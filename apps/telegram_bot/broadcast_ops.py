"""Shared broadcast confirm/send machinery for Django admin and the bot admin panel.

One source of truth for what "confirmed and safe to send" means: the frozen
recipient snapshot, the digest that binds a confirmation to it, and the
draft → confirming → queued transitions. Both surfaces call these functions
rather than each reimplementing the guarantee — the same principle
`apps.subscriptions.catalog`'s country list follows for why it is read back
rather than recomputed twice.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from uuid import uuid4

from django.db import transaction

from .models import Broadcast, BroadcastDelivery


# Same bounds as `Broadcast.message`'s own validators; kept here as the single
# place both the Django admin form and the bot's text prompts read them from.
MESSAGE_MIN_LENGTH = 10
MESSAGE_MAX_LENGTH = 4096


def confirmation_digest(broadcast: Broadcast) -> str:
    """Bind confirmation to content, audience controls, and immutable recipient snapshot."""
    values = (
        str(broadcast.pk), broadcast.message, broadcast.audience,
        str(broadcast.include_subscription_button), str(broadcast.preview_snapshot_id),
        str(broadcast.total_users),
    )
    return sha256('\0'.join(values).encode()).hexdigest()


def create_preview_snapshot(broadcast_id: int) -> Broadcast | None:
    """Atomically replace any old preview with a fixed recipient ledger."""
    with transaction.atomic():
        broadcast = Broadcast.objects.select_for_update().filter(pk=broadcast_id, status='draft').first()
        if not broadcast:
            return None
        BroadcastDelivery.objects.filter(broadcast=broadcast).delete()
        BroadcastDelivery.objects.bulk_create(
            [BroadcastDelivery(broadcast=broadcast, user=user) for user in broadcast.recipient_queryset().iterator()],
            batch_size=1000,
        )
        broadcast.preview_snapshot_id = uuid4()
        broadcast.total_users = BroadcastDelivery.objects.filter(broadcast=broadcast).count()
        broadcast.status = 'confirming'
        broadcast.error_message = ''
        broadcast.save(update_fields=[
            'preview_snapshot_id', 'total_users', 'status', 'error_message', 'updated_at',
        ])
        return broadcast


def enqueue_after_commit(broadcast_id: int) -> None:
    from .tasks import safe_broadcast_v1
    transaction.on_commit(lambda: safe_broadcast_v1.delay(broadcast_id))


@dataclass(frozen=True)
class QueueResult:
    ok: bool
    error: str = ''  # '', 'stale', 'snapshot_corrupt'


def queue_confirmed_broadcast(broadcast_id: int, digest: str) -> QueueResult:
    """Move a confirmed broadcast from ``confirming`` to ``queued`` and enqueue delivery.

    ``digest`` must match ``confirmation_digest`` of the current row: a stale
    or tampered confirmation is refused rather than trusted, exactly as the
    Django admin confirm page's hidden field is checked today.
    """
    with transaction.atomic():
        broadcast = Broadcast.objects.select_for_update().filter(pk=broadcast_id, status='confirming').first()
        if not broadcast or not broadcast.preview_snapshot_id or digest != confirmation_digest(broadcast):
            return QueueResult(ok=False, error='stale')
        snapshot_count = BroadcastDelivery.objects.filter(broadcast=broadcast).count()
        if snapshot_count != broadcast.total_users:
            return QueueResult(ok=False, error='snapshot_corrupt')
        broadcast.status = 'queued'
        broadcast.error_message = ''
        broadcast.heartbeat_at = None
        broadcast.save(update_fields=['status', 'error_message', 'heartbeat_at', 'updated_at'])
        enqueue_after_commit(broadcast.id)
    return QueueResult(ok=True)


def cancel_confirming_broadcast(broadcast_id: int) -> bool:
    """Return a confirming broadcast to draft and drop its snapshot."""
    with transaction.atomic():
        broadcast = Broadcast.objects.select_for_update().filter(pk=broadcast_id, status='confirming').first()
        if not broadcast:
            return False
        BroadcastDelivery.objects.filter(broadcast=broadcast).delete()
        broadcast.status = 'draft'
        broadcast.preview_snapshot_id = None
        broadcast.total_users = 0
        broadcast.save(update_fields=['status', 'preview_snapshot_id', 'total_users', 'updated_at'])
    return True
