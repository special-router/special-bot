from django.db import models


class ProviderCredentialModeChoices(models.TextChoices):
    SHARED = 'shared', 'Shared'
    PER_USER = 'per_user', 'Per user'


class ProviderLeaseStateChoices(models.TextChoices):
    PROVISIONING = 'provisioning', 'Provisioning'
    ACTIVE = 'active', 'Active'
    SUSPENDED = 'suspended', 'Suspended'
    REVOKED = 'revoked', 'Revoked'
    FAILED = 'failed', 'Failed'


class ProviderInventoryStateChoices(models.TextChoices):
    QUARANTINED = 'quarantined', 'Quarantined'
    CANARY = 'canary', 'Canary'
    ACTIVE = 'active', 'Active'
    SUPERSEDED = 'superseded', 'Superseded'
    REJECTED = 'rejected', 'Rejected'


class ProviderProtocolChoices(models.TextChoices):
    VLESS = 'vless', 'VLESS'
    HYSTERIA2 = 'hysteria2', 'Hysteria2'
    TROJAN = 'trojan', 'Trojan'
    SHADOWSOCKS = 'shadowsocks', 'Shadowsocks'
    WIREGUARD = 'wireguard', 'WireGuard'


class ProviderProbeStateChoices(models.TextChoices):
    HEALTHY = 'healthy', 'Healthy'
    UNHEALTHY = 'unhealthy', 'Unhealthy'


class CompiledSnapshotStateChoices(models.TextChoices):
    ACTIVE = 'active', 'Active'
    SUPERSEDED = 'superseded', 'Superseded'
    REVOKED = 'revoked', 'Revoked'
