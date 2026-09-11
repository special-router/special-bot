from __future__ import annotations

import ipaddress
import re
from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from apps.providers.choices import (
    ProviderInventoryStateChoices,
    ProviderProbeStateChoices,
    ProviderProtocolChoices,
)
from apps.providers.models import (
    Provider,
    ProviderEndpoint,
    ProviderInventoryVersion,
    ProviderProbeResult,
)
from apps.providers.types import CanonicalEndpoint


_DIGEST_RE = re.compile(r'^[0-9a-f]{64}$')
_HOST_RE = re.compile(
    r'^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+'
    r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$',
)
_SAFE_TOKEN_RE = re.compile(r'^[a-zA-Z0-9._:@+-]{1,128}$')
_VANTAGE_RE = re.compile(r'^[a-z][a-z0-9_-]{1,31}$')
_SUPPORTED_TRANSPORTS = frozenset({'tcp', 'grpc', 'ws', 'xhttp', 'udp'})
_SUPPORTED_SECURITY = frozenset({'tls', 'reality'})


def _valid_host(value: str) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(character in value for character in '\r\n\t /?#@')
    ):
        return False
    try:
        return ipaddress.ip_address(value).is_global
    except ValueError:
        return bool(_HOST_RE.fullmatch(value.casefold()))


def _validate_endpoint(endpoint: CanonicalEndpoint) -> None:
    errors = []
    if not isinstance(endpoint, CanonicalEndpoint):
        raise ValidationError({'endpoint': 'invalid_type'})
    if not isinstance(endpoint.external_id, str) or not _SAFE_TOKEN_RE.fullmatch(endpoint.external_id):
        errors.append('external_id')
    if not isinstance(endpoint.protocol, str) or endpoint.protocol not in ProviderProtocolChoices.values:
        errors.append('protocol')
    if not isinstance(endpoint.transport, str) or endpoint.transport not in _SUPPORTED_TRANSPORTS:
        errors.append('transport')
    if not isinstance(endpoint.security, str) or endpoint.security not in _SUPPORTED_SECURITY:
        errors.append('security')
    if not _valid_host(endpoint.host):
        errors.append('host')
    if not isinstance(endpoint.port, int) or isinstance(endpoint.port, bool) or not 1 <= endpoint.port <= 65535:
        errors.append('port')
    if not isinstance(endpoint.server_name, str) or (endpoint.server_name and not _valid_host(endpoint.server_name)):
        errors.append('server_name')
    if not isinstance(endpoint.path, str) or (
        endpoint.path
        and (
            len(endpoint.path) > 512
            or not endpoint.path.startswith('/')
            or any(character in endpoint.path for character in '\r\n\t')
        )
    ):
        errors.append('path')
    bounded_fields = (
        ('region', endpoint.region, 32),
        ('service_name', endpoint.service_name, 255),
        ('public_key', endpoint.public_key, 128),
        ('short_id', endpoint.short_id, 32),
        ('fingerprint', endpoint.fingerprint, 32),
        ('flow', endpoint.flow, 64),
        ('host_header', endpoint.host_header, 253),
        ('transport_mode', endpoint.transport_mode, 32),
        ('alpn', endpoint.alpn, 128),
    )
    for field_name, value, max_length in bounded_fields:
        if (
            not isinstance(value, str)
            or len(value) > max_length
            or any(character in value for character in '\r\n\t')
        ):
            errors.append(field_name)
    if endpoint.host_header and not _valid_host(endpoint.host_header):
        errors.append('host_header')
    if endpoint.security == 'reality' and (not endpoint.server_name or not endpoint.public_key):
        errors.append('reality')
    if endpoint.security == 'tls' and not endpoint.server_name:
        errors.append('tls')
    if errors:
        raise ValidationError({'endpoint': errors})


def _validate_digest(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise ValidationError({field_name: 'invalid_sha256'})


def _allowed_endpoint_hosts(provider: Provider) -> set[str]:
    capabilities = provider.capabilities
    if not isinstance(capabilities, dict):
        raise ValidationError({'provider': 'capabilities_invalid'})
    hosts = capabilities.get('allowed_endpoint_hosts')
    if (
        not isinstance(hosts, list)
        or not hosts
        or len(hosts) > 512
        or any(not _valid_host(host) for host in hosts)
    ):
        raise ValidationError({'provider': 'endpoint_allowlist_invalid'})
    normalized = {host.casefold() for host in hosts}
    if len(normalized) != len(hosts):
        raise ValidationError({'provider': 'endpoint_allowlist_invalid'})
    return normalized


def stage_inventory(
    *,
    provider_id: int,
    source_digest: str,
    payload_digest: str,
    endpoints: tuple[CanonicalEndpoint, ...],
    fetched_at: datetime,
    expires_at: datetime | None = None,
) -> ProviderInventoryVersion:
    _validate_digest(source_digest, 'source_digest')
    _validate_digest(payload_digest, 'payload_digest')
    if not isinstance(fetched_at, datetime) or not timezone.is_aware(fetched_at):
        raise ValidationError({'fetched_at': 'invalid'})
    if expires_at is not None and (
        not isinstance(expires_at, datetime)
        or not timezone.is_aware(expires_at)
        or expires_at <= fetched_at
    ):
        raise ValidationError({'expires_at': 'not_after_fetch'})
    if not endpoints:
        raise ValidationError({'endpoints': 'empty_inventory'})
    for endpoint in endpoints:
        _validate_endpoint(endpoint)
    external_ids = [endpoint.external_id for endpoint in endpoints]
    if len(external_ids) != len(set(external_ids)):
        raise ValidationError({'endpoints': 'duplicate_external_id'})

    with transaction.atomic():
        provider = Provider.objects.select_for_update().get(pk=provider_id)
        if not provider.enabled:
            raise ValidationError({'provider': 'disabled'})
        allowed_hosts = _allowed_endpoint_hosts(provider)
        if any(endpoint.host.casefold() not in allowed_hosts for endpoint in endpoints):
            raise ValidationError({'endpoints': 'host_not_allowed'})
        current_revision = (
            ProviderInventoryVersion.objects.filter(provider=provider)
            .aggregate(value=Max('revision'))['value']
            or 0
        )
        inventory = ProviderInventoryVersion.objects.create(
            provider=provider,
            revision=current_revision + 1,
            source_digest=source_digest,
            payload_digest=payload_digest,
            state=ProviderInventoryStateChoices.QUARANTINED,
            endpoint_count=len(endpoints),
            fetched_at=fetched_at,
            validated_at=timezone.now(),
            expires_at=expires_at,
        )
        ProviderEndpoint.objects.bulk_create([
            ProviderEndpoint(
                inventory=inventory,
                external_id=endpoint.external_id,
                protocol=endpoint.protocol,
                transport=endpoint.transport,
                security=endpoint.security,
                host=endpoint.host.casefold(),
                port=endpoint.port,
                region=endpoint.region,
                server_name=endpoint.server_name.casefold(),
                path=endpoint.path,
                service_name=endpoint.service_name,
                public_key=endpoint.public_key,
                short_id=endpoint.short_id,
                fingerprint=endpoint.fingerprint,
                flow=endpoint.flow,
                host_header=endpoint.host_header,
                transport_mode=endpoint.transport_mode,
                alpn=endpoint.alpn,
            )
            for endpoint in endpoints
        ])
        return inventory


def promote_inventory(
    inventory_id: int,
    *,
    required_vantages: tuple[str, ...],
    allow_canary: bool = False,
    max_probe_age: timedelta = timedelta(minutes=15),
) -> ProviderInventoryVersion:
    if (
        not required_vantages
        or len(required_vantages) != len(set(required_vantages))
        or any(not isinstance(vantage, str) or not _VANTAGE_RE.fullmatch(vantage) for vantage in required_vantages)
    ):
        raise ValidationError({'required_vantages': 'invalid'})
    if (
        not isinstance(max_probe_age, timedelta)
        or max_probe_age <= timedelta(0)
        or max_probe_age > timedelta(hours=1)
    ):
        raise ValidationError({'max_probe_age': 'invalid'})
    provider_id = ProviderInventoryVersion.objects.values_list('provider_id', flat=True).get(pk=inventory_id)
    with transaction.atomic():
        provider = Provider.objects.select_for_update().get(pk=provider_id)
        inventory = (
            ProviderInventoryVersion.objects.select_for_update()
            .prefetch_related('endpoints')
            .get(pk=inventory_id, provider=provider)
        )
        if not provider.enabled:
            raise ValidationError({'provider': 'disabled'})
        if not allow_canary and not provider.production_ready:
            raise ValidationError({'provider': 'admission_incomplete'})
        allowed_hosts = _allowed_endpoint_hosts(provider)
        now = timezone.now()
        if inventory.expires_at is not None and inventory.expires_at <= now:
            raise ValidationError({'inventory': 'expired'})
        target_state = (
            ProviderInventoryStateChoices.CANARY
            if allow_canary
            else ProviderInventoryStateChoices.ACTIVE
        )
        promotable_states = {
            ProviderInventoryStateChoices.QUARANTINED,
            target_state,
        }
        if not allow_canary:
            promotable_states.add(ProviderInventoryStateChoices.CANARY)
        if inventory.state not in promotable_states:
            raise ValidationError({'inventory': 'not_promotable'})
        endpoints = list(inventory.endpoints.all())
        if inventory.validated_at is None or not endpoints or inventory.endpoint_count != len(endpoints):
            raise ValidationError({'inventory': 'incomplete'})
        if any(endpoint.host.casefold() not in allowed_hosts for endpoint in endpoints):
            raise ValidationError({'inventory': 'host_not_allowed'})
        fresh_after = max(inventory.validated_at, now - max_probe_age)
        for endpoint in endpoints:
            for vantage in required_vantages:
                healthy = ProviderProbeResult.objects.filter(
                    endpoint=endpoint,
                    lease__isnull=True,
                    credential_revision=0,
                    vantage=vantage,
                    state=ProviderProbeStateChoices.HEALTHY,
                    observed_egress__isnull=False,
                    checked_at__gte=fresh_after,
                ).exists()
                if not healthy:
                    raise ValidationError({'inventory': 'probe_incomplete'})
        if inventory.state == target_state:
            return inventory
        ProviderInventoryVersion.objects.filter(
            provider=provider,
            state=target_state,
        ).exclude(pk=inventory.pk).update(state=ProviderInventoryStateChoices.SUPERSEDED)
        inventory.state = target_state
        inventory.published_at = now
        inventory.save(update_fields=('state', 'published_at'))
        return inventory


def active_inventory(provider_id: int) -> ProviderInventoryVersion | None:
    return (
        ProviderInventoryVersion.objects.filter(
            provider_id=provider_id,
            provider__enabled=True,
            state=ProviderInventoryStateChoices.ACTIVE,
        )
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
        .prefetch_related('endpoints')
        .first()
    )
