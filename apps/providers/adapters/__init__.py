from .base import (
    ProviderAdapter,
    ProviderCapabilities,
    ProviderConfigurationError,
    ProviderInventory,
    ProviderLeasePayload,
    ProviderOperationUnsupported,
    ProviderTemporaryError,
    ProviderUsage,
)
from .builtin import AServiceAdapter, VPNStarAdapter
from .registry import adapter_for_provider, register_adapter, unregister_adapter


__all__ = (
    'ProviderAdapter',
    'ProviderCapabilities',
    'ProviderConfigurationError',
    'ProviderInventory',
    'ProviderLeasePayload',
    'ProviderOperationUnsupported',
    'ProviderTemporaryError',
    'ProviderUsage',
    'AServiceAdapter',
    'VPNStarAdapter',
    'adapter_for_provider',
    'register_adapter',
    'unregister_adapter',
)
