from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.subscriptions.models import SubscriptionAccessToken
from apps.vpn.models import UserVPN


_TOKEN_RE = re.compile(r'^sp1_[A-Za-z0-9_-]{32,96}$')
MAX_ROTATION_OVERLAP = timedelta(hours=1)


def access_token_digest(raw_token: str) -> str:
    if not isinstance(raw_token, str) or not _TOKEN_RE.fullmatch(raw_token):
        raise ValidationError('invalid_access_token')
    return hashlib.sha256(raw_token.encode('ascii')).hexdigest()


@transaction.atomic
def issue_access_token(
    subscription: UserVPN,
    *,
    active_from=None,
    expires_at=None,
) -> tuple[str, SubscriptionAccessToken]:
    if not isinstance(subscription, UserVPN) or subscription.pk is None:
        raise ValidationError('invalid_subscription')
    subscription = UserVPN.objects.select_for_update().get(pk=subscription.pk)
    if not subscription.enabled:
        raise ValidationError('disabled_subscription')
    active_from = active_from or timezone.now()
    if not isinstance(active_from, datetime) or not timezone.is_aware(active_from):
        raise ValidationError('invalid_access_token_start')
    if expires_at is not None and (
        not isinstance(expires_at, datetime)
        or not timezone.is_aware(expires_at)
        or expires_at <= active_from
    ):
        raise ValidationError('invalid_access_token_expiry')
    raw_token = f'sp1_{secrets.token_urlsafe(32)}'
    digest = access_token_digest(raw_token)
    record = SubscriptionAccessToken.objects.create(
        subscription=subscription,
        token_hash=digest,
        token_hint=digest[:12],
        active_from=active_from,
        expires_at=expires_at,
    )
    return raw_token, record


def resolve_access_token(raw_token: str, *, touch: bool = False) -> SubscriptionAccessToken | None:
    try:
        digest = access_token_digest(raw_token)
    except ValidationError:
        return None
    now = timezone.now()
    record = (
        SubscriptionAccessToken.objects.select_related('subscription')
        .filter(
            token_hash=digest,
            active_from__lte=now,
            revoked_at__isnull=True,
            subscription__enabled=True,
        )
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .first()
    )
    if record is not None and touch:
        SubscriptionAccessToken.objects.filter(pk=record.pk).update(last_seen_at=now)
        record.last_seen_at = now
    return record


@transaction.atomic
def rotate_access_token(
    subscription_id: int,
    *,
    overlap: timedelta = timedelta(minutes=15),
) -> tuple[str, SubscriptionAccessToken]:
    if not isinstance(overlap, timedelta) or overlap < timedelta(0) or overlap > MAX_ROTATION_OVERLAP:
        raise ValidationError('invalid_access_token_overlap')
    subscription = UserVPN.objects.select_for_update().get(pk=subscription_id)
    now = timezone.now()
    active_tokens = list(
        SubscriptionAccessToken.objects.select_for_update()
        .filter(subscription=subscription, revoked_at__isnull=True)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    )
    if overlap == timedelta(0):
        for token in active_tokens:
            token.revoked_at = now
            token.save(update_fields=('revoked_at',))
    else:
        overlap_until = now + overlap
        for token in active_tokens:
            if token.expires_at is None or token.expires_at > overlap_until:
                token.expires_at = overlap_until
                token.save(update_fields=('expires_at',))
    return issue_access_token(subscription, active_from=now)


@transaction.atomic
def revoke_access_tokens(subscription_id: int) -> int:
    UserVPN.objects.select_for_update().get(pk=subscription_id)
    return SubscriptionAccessToken.objects.filter(
        subscription_id=subscription_id,
        revoked_at__isnull=True,
    ).update(revoked_at=timezone.now())
