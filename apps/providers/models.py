from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.providers.choices import (
    CompiledSnapshotStateChoices,
    ProviderCredentialModeChoices,
    ProviderInventoryStateChoices,
    ProviderLeaseStateChoices,
    ProviderProbeStateChoices,
    ProviderProtocolChoices,
)


SHA256_VALIDATOR = RegexValidator(r'^[0-9a-f]{64}$', 'Expected a lowercase SHA-256 digest.')


class Provider(models.Model):
    slug = models.SlugField(
        'Stable provider id',
        max_length=32,
        unique=True,
    )

    name = models.CharField(
        'Provider name',
        max_length=128,
    )

    adapter = models.SlugField(
        'Registered adapter id',
        max_length=64,
    )

    credential_mode = models.CharField(
        'Credential isolation',
        max_length=16,
        choices=ProviderCredentialModeChoices.choices,
        default=ProviderCredentialModeChoices.SHARED,
    )

    enabled = models.BooleanField(
        'Enabled',
        default=False,
    )

    per_user_lifecycle = models.BooleanField(
        'Supports per-user lifecycle',
        default=False,
    )

    usage_tracking = models.BooleanField(
        'Supports quota and expiry sync',
        default=False,
    )

    resale_confirmed_at = models.DateTimeField(
        'Resale rights confirmed',
        null=True,
        blank=True,
    )

    priority = models.PositiveSmallIntegerField(
        'Selection priority',
        default=100,
    )

    capabilities = models.JSONField(
        'Safe capability metadata',
        default=dict,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('priority', 'slug')
        constraints = [
            models.CheckConstraint(
                condition=Q(priority__lte=1000),
                name='provider_priority_lte_1000',
            ),
        ]

    @property
    def production_ready(self) -> bool:
        return bool(
            self.enabled
            and self.resale_confirmed_at
            and self.per_user_lifecycle
            and self.usage_tracking
            and self.credential_mode != ProviderCredentialModeChoices.SHARED
        )

    def __str__(self):
        return self.slug


class ProviderLease(models.Model):
    provider = models.ForeignKey(
        Provider,
        on_delete=models.PROTECT,
        related_name='leases',
    )

    subscription = models.ForeignKey(
        'vpn.UserVPN',
        on_delete=models.CASCADE,
        related_name='provider_leases',
    )

    external_id = models.CharField(
        'Provider-side lease id',
        max_length=255,
        blank=True,
        default='',
    )

    secret_reference = models.UUIDField(
        'External secret reference',
        null=True,
        blank=True,
    )

    credential_revision = models.PositiveIntegerField(
        'Credential revision',
        default=1,
    )

    state = models.CharField(
        'Lifecycle state',
        max_length=16,
        choices=ProviderLeaseStateChoices.choices,
        default=ProviderLeaseStateChoices.PROVISIONING,
    )

    expires_at = models.DateTimeField(
        'Provider expiry',
        null=True,
        blank=True,
    )

    quota_limit_bytes = models.PositiveBigIntegerField(
        'Provider quota',
        null=True,
        blank=True,
    )

    quota_used_bytes = models.PositiveBigIntegerField(
        'Provider usage',
        null=True,
        blank=True,
    )

    last_synced_at = models.DateTimeField(
        'Last provider sync',
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('provider', 'subscription'),
                name='unique_provider_lease_per_subscription',
            ),
            models.UniqueConstraint(
                fields=('provider', 'external_id'),
                condition=~Q(external_id=''),
                name='unique_external_provider_lease',
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(state=ProviderLeaseStateChoices.ACTIVE)
                    | (~Q(external_id='') & Q(secret_reference__isnull=False))
                ),
                name='active_provider_lease_has_references',
            ),
        ]

    def __str__(self):
        return f'{self.provider_id}:{self.subscription_id}:{self.state}'


class ProviderInventoryVersion(models.Model):
    provider = models.ForeignKey(
        Provider,
        on_delete=models.PROTECT,
        related_name='inventory_versions',
    )

    revision = models.PositiveBigIntegerField(
        'Provider-local revision',
    )

    source_digest = models.CharField(
        'Safe source-set digest',
        max_length=64,
        validators=(SHA256_VALIDATOR,),
    )

    payload_digest = models.CharField(
        'Normalized payload digest',
        max_length=64,
        validators=(SHA256_VALIDATOR,),
    )

    state = models.CharField(
        'Publication state',
        max_length=16,
        choices=ProviderInventoryStateChoices.choices,
        default=ProviderInventoryStateChoices.QUARANTINED,
    )

    endpoint_count = models.PositiveIntegerField(
        'Normalized endpoint count',
        default=0,
    )

    fetched_at = models.DateTimeField(
        'Fetched at',
        default=timezone.now,
    )

    validated_at = models.DateTimeField(
        'Validated at',
        null=True,
        blank=True,
    )

    published_at = models.DateTimeField(
        'Published at',
        null=True,
        blank=True,
    )

    expires_at = models.DateTimeField(
        'Inventory expiry',
        null=True,
        blank=True,
    )

    error_class = models.CharField(
        'Safe rejection class',
        max_length=64,
        blank=True,
        default='',
    )

    class Meta:
        ordering = ('provider_id', '-revision')
        constraints = [
            models.UniqueConstraint(
                fields=('provider', 'revision'),
                name='unique_provider_inventory_revision',
            ),
            models.UniqueConstraint(
                fields=('provider',),
                condition=Q(state=ProviderInventoryStateChoices.ACTIVE),
                name='unique_active_provider_inventory',
            ),
            models.UniqueConstraint(
                fields=('provider',),
                condition=Q(state=ProviderInventoryStateChoices.CANARY),
                name='unique_canary_provider_inventory',
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(state__in=(
                        ProviderInventoryStateChoices.ACTIVE,
                        ProviderInventoryStateChoices.CANARY,
                    ))
                    | (Q(endpoint_count__gt=0) & Q(published_at__isnull=False))
                ),
                name='active_provider_inventory_is_published',
            ),
        ]

    def __str__(self):
        return f'{self.provider_id}:{self.revision}:{self.state}'


class ProviderEndpoint(models.Model):
    inventory = models.ForeignKey(
        ProviderInventoryVersion,
        on_delete=models.CASCADE,
        related_name='endpoints',
    )

    external_id = models.CharField(
        'Stable provider endpoint id',
        max_length=128,
    )

    protocol = models.CharField(
        'VPN protocol',
        max_length=16,
        choices=ProviderProtocolChoices.choices,
    )

    transport = models.CharField(
        'Transport',
        max_length=16,
    )

    security = models.CharField(
        'Security mode',
        max_length=16,
    )

    host = models.CharField(
        'Data-plane host',
        max_length=253,
    )

    port = models.PositiveIntegerField(
        'Data-plane port',
    )

    region = models.CharField(
        'Region',
        max_length=32,
        blank=True,
        default='',
    )

    server_name = models.CharField(
        'TLS server name',
        max_length=253,
        blank=True,
        default='',
    )

    path = models.CharField(
        'HTTP path',
        max_length=512,
        blank=True,
        default='',
    )

    service_name = models.CharField(
        'gRPC service name',
        max_length=255,
        blank=True,
        default='',
    )

    public_key = models.CharField(
        'Reality public key',
        max_length=128,
        blank=True,
        default='',
    )

    short_id = models.CharField(
        'Reality short id',
        max_length=32,
        blank=True,
        default='',
    )

    fingerprint = models.CharField(
        'TLS fingerprint',
        max_length=32,
        blank=True,
        default='',
    )

    flow = models.CharField(
        'VLESS flow',
        max_length=64,
        blank=True,
        default='',
    )

    host_header = models.CharField(
        'HTTP host header',
        max_length=253,
        blank=True,
        default='',
    )

    transport_mode = models.CharField(
        'Transport mode',
        max_length=32,
        blank=True,
        default='',
    )

    alpn = models.CharField(
        'TLS ALPN',
        max_length=128,
        blank=True,
        default='',
    )

    class Meta:
        ordering = ('inventory_id', 'external_id')
        constraints = [
            models.UniqueConstraint(
                fields=('inventory', 'external_id'),
                name='unique_endpoint_per_inventory',
            ),
            models.CheckConstraint(
                condition=Q(port__gte=1) & Q(port__lte=65535),
                name='provider_endpoint_valid_port',
            ),
        ]

    def __str__(self):
        return f'{self.inventory_id}:{self.external_id}'


class ProviderProbeResult(models.Model):
    endpoint = models.ForeignKey(
        ProviderEndpoint,
        on_delete=models.CASCADE,
        related_name='probe_results',
    )

    lease = models.ForeignKey(
        ProviderLease,
        on_delete=models.SET_NULL,
        related_name='probe_results',
        null=True,
        blank=True,
    )

    credential_revision = models.PositiveIntegerField(
        'Credential revision',
    )

    vantage = models.CharField(
        'Probe vantage',
        max_length=64,
    )

    state = models.CharField(
        'Probe state',
        max_length=16,
        choices=ProviderProbeStateChoices.choices,
    )

    observed_egress = models.GenericIPAddressField(
        'Observed tunnel egress',
        null=True,
        blank=True,
    )

    latency_ms = models.PositiveIntegerField(
        'End-to-end latency',
        null=True,
        blank=True,
    )

    error_class = models.CharField(
        'Safe error class',
        max_length=64,
        blank=True,
        default='',
    )

    checked_at = models.DateTimeField(
        'Checked at',
        default=timezone.now,
    )

    class Meta:
        ordering = ('endpoint_id', 'vantage', '-checked_at')
        constraints = [
            models.UniqueConstraint(
                fields=('endpoint', 'credential_revision', 'vantage'),
                condition=Q(lease__isnull=True),
                name='unique_shared_provider_probe_verdict',
            ),
            models.UniqueConstraint(
                fields=('endpoint', 'lease', 'credential_revision', 'vantage'),
                condition=Q(lease__isnull=False),
                name='unique_leased_provider_probe_verdict',
            ),
        ]

    def __str__(self):
        return f'{self.endpoint_id}:{self.credential_revision}:{self.vantage}'


class CompiledSnapshot(models.Model):
    subscription = models.ForeignKey(
        'vpn.UserVPN',
        on_delete=models.CASCADE,
        related_name='compiled_snapshots',
    )

    client_family = models.CharField(
        'Client renderer family',
        max_length=32,
    )

    revision = models.PositiveBigIntegerField(
        'Subscription-local revision',
    )

    state = models.CharField(
        'Publication state',
        max_length=16,
        choices=CompiledSnapshotStateChoices.choices,
        default=CompiledSnapshotStateChoices.ACTIVE,
    )

    storage_key = models.CharField(
        'Immutable object reference',
        max_length=512,
        unique=True,
    )

    content_digest = models.CharField(
        'Compiled content digest',
        max_length=64,
        validators=(SHA256_VALIDATOR,),
    )

    inventory_digest = models.CharField(
        'Input inventory digest',
        max_length=64,
        validators=(SHA256_VALIDATOR,),
    )

    generated_at = models.DateTimeField(
        'Generated at',
        default=timezone.now,
    )

    published_at = models.DateTimeField(
        'Published at',
        default=timezone.now,
    )

    expires_at = models.DateTimeField(
        'Effective access expiry',
    )

    class Meta:
        ordering = ('subscription_id', 'client_family', '-revision')
        constraints = [
            models.UniqueConstraint(
                fields=('subscription', 'client_family', 'revision'),
                name='unique_compiled_snapshot_revision',
            ),
            models.UniqueConstraint(
                fields=('subscription', 'client_family'),
                condition=Q(state=CompiledSnapshotStateChoices.ACTIVE),
                name='unique_active_compiled_snapshot',
            ),
            models.CheckConstraint(
                condition=Q(expires_at__gt=models.F('generated_at')),
                name='compiled_snapshot_expiry_after_generation',
            ),
        ]

    def __str__(self):
        return f'{self.subscription_id}:{self.client_family}:{self.revision}'
