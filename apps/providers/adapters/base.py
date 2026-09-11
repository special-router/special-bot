from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from apps.providers.types import CanonicalEndpoint


if TYPE_CHECKING:
    from apps.providers.models import Provider, ProviderLease
    from apps.vpn.models import UserVPN


class ProviderError(Exception):
    pass


class ProviderConfigurationError(ProviderError):
    pass


class ProviderTemporaryError(ProviderError):
    pass


class ProviderOperationUnsupported(ProviderConfigurationError):
    pass


@dataclass(frozen=True)
class ProviderCapabilities:
    credential_mode: str
    protocols: tuple[str, ...]
    regions: tuple[str, ...] = ()
    supports_create: bool = False
    supports_suspend: bool = False
    supports_resume: bool = False
    supports_rotate: bool = False
    supports_revoke: bool = False
    supports_usage: bool = False
    max_devices: int | None = None

    @property
    def supports_full_lifecycle(self) -> bool:
        return all((
            self.supports_create,
            self.supports_suspend,
            self.supports_resume,
            self.supports_rotate,
            self.supports_revoke,
        ))


@dataclass(frozen=True)
class ProviderLeasePayload:
    external_id: str
    secret_reference: UUID
    credential_revision: int = 1
    expires_at: datetime | None = None

    def __post_init__(self):
        if not isinstance(self.external_id, str) or not self.external_id or len(self.external_id) > 255:
            raise ProviderConfigurationError('lease_external_id_invalid')
        if not isinstance(self.secret_reference, UUID):
            raise ProviderConfigurationError('lease_secret_reference_invalid')
        if (
            not isinstance(self.credential_revision, int)
            or isinstance(self.credential_revision, bool)
            or self.credential_revision < 1
        ):
            raise ProviderConfigurationError('lease_credential_revision_invalid')


@dataclass(frozen=True)
class ProviderUsage:
    used_bytes: int | None = None
    limit_bytes: int | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True)
class ProviderInventory:
    source_digest: str
    payload_digest: str
    endpoints: tuple[CanonicalEndpoint, ...]
    fetched_at: datetime
    expires_at: datetime | None = None


class ProviderAdapter(ABC):
    def __init__(self, provider: Provider):
        self.provider = provider

    @property
    @abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        raise NotImplementedError

    @abstractmethod
    def fetch_inventory(self) -> ProviderInventory:
        raise NotImplementedError

    def create_lease(self, subscription: UserVPN) -> ProviderLeasePayload:
        raise ProviderOperationUnsupported('create_lease')

    def suspend_lease(self, lease: ProviderLease) -> None:
        raise ProviderOperationUnsupported('suspend_lease')

    def resume_lease(self, lease: ProviderLease) -> None:
        raise ProviderOperationUnsupported('resume_lease')

    def rotate_lease(self, lease: ProviderLease) -> ProviderLeasePayload:
        raise ProviderOperationUnsupported('rotate_lease')

    def revoke_lease(self, lease: ProviderLease) -> None:
        raise ProviderOperationUnsupported('revoke_lease')

    def fetch_usage(self, lease: ProviderLease) -> ProviderUsage:
        raise ProviderOperationUnsupported('fetch_usage')
