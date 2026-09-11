from __future__ import annotations

import re
from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.providers.choices import CompiledSnapshotStateChoices
from apps.providers.models import CompiledSnapshot
from apps.providers.services.storage import ImmutableSnapshotStore, SnapshotArtifact
from apps.vpn.models import UserVPN


_DIGEST_RE = re.compile(r'^[0-9a-f]{64}$')
_CLIENT_FAMILY_RE = re.compile(r'^[a-z][a-z0-9_-]{0,31}$')


def publish_snapshot(
    *,
    subscription_id: int,
    client_family: str,
    storage_key: str,
    content_digest: str,
    inventory_digest: str,
    expires_at: datetime,
    store: ImmutableSnapshotStore,
) -> CompiledSnapshot:
    if not isinstance(client_family, str) or not _CLIENT_FAMILY_RE.fullmatch(client_family):
        raise ValidationError({'client_family': 'invalid'})
    if (
        not isinstance(storage_key, str)
        or not storage_key
        or len(storage_key) > 512
        or any(character in storage_key for character in '\r\n\t')
    ):
        raise ValidationError({'storage_key': 'invalid'})
    if not isinstance(content_digest, str) or not _DIGEST_RE.fullmatch(content_digest):
        raise ValidationError({'content_digest': 'invalid_sha256'})
    if not isinstance(inventory_digest, str) or not _DIGEST_RE.fullmatch(inventory_digest):
        raise ValidationError({'inventory_digest': 'invalid_sha256'})
    if (
        not isinstance(expires_at, datetime)
        or not timezone.is_aware(expires_at)
        or expires_at <= timezone.now()
    ):
        raise ValidationError({'expires_at': 'not_future'})
    try:
        artifact = store.inspect(storage_key)
    except Exception:
        raise ValidationError({'storage_key': 'artifact_unavailable'}) from None
    if (
        not isinstance(artifact, SnapshotArtifact)
        or artifact.size < 1
        or artifact.content_digest != content_digest
    ):
        raise ValidationError({'storage_key': 'artifact_mismatch'})

    with transaction.atomic():
        subscription = UserVPN.objects.select_for_update().get(pk=subscription_id)
        if not subscription.enabled:
            raise ValidationError({'subscription': 'disabled'})
        current_revision = (
            CompiledSnapshot.objects.filter(
                subscription=subscription,
                client_family=client_family,
            ).aggregate(value=Max('revision'))['value']
            or 0
        )
        CompiledSnapshot.objects.filter(
            subscription=subscription,
            client_family=client_family,
            state=CompiledSnapshotStateChoices.ACTIVE,
        ).update(state=CompiledSnapshotStateChoices.SUPERSEDED)
        return CompiledSnapshot.objects.create(
            subscription=subscription,
            client_family=client_family,
            revision=current_revision + 1,
            state=CompiledSnapshotStateChoices.ACTIVE,
            storage_key=storage_key,
            content_digest=content_digest,
            inventory_digest=inventory_digest,
            expires_at=expires_at,
        )


def revoke_snapshots(subscription_id: int) -> int:
    with transaction.atomic():
        UserVPN.objects.select_for_update().get(pk=subscription_id)
        return CompiledSnapshot.objects.filter(
            subscription_id=subscription_id,
            state=CompiledSnapshotStateChoices.ACTIVE,
        ).update(state=CompiledSnapshotStateChoices.REVOKED)
