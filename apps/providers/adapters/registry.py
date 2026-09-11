from __future__ import annotations

from collections.abc import Callable

from apps.providers.adapters.base import ProviderAdapter, ProviderConfigurationError
from apps.providers.models import Provider


AdapterFactory = Callable[[Provider], ProviderAdapter]
_ADAPTERS: dict[str, AdapterFactory] = {}


def register_adapter(name: str, factory: AdapterFactory) -> None:
    normalized = name.strip().casefold()
    if not normalized or normalized in _ADAPTERS:
        raise ProviderConfigurationError('adapter_registration_conflict')
    _ADAPTERS[normalized] = factory


def unregister_adapter(name: str) -> None:
    _ADAPTERS.pop(name.strip().casefold(), None)


def adapter_for_provider(provider: Provider) -> ProviderAdapter:
    factory = _ADAPTERS.get(provider.adapter.casefold())
    if factory is None:
        raise ProviderConfigurationError('adapter_not_registered')
    adapter = factory(provider)
    if not isinstance(adapter, ProviderAdapter):
        raise ProviderConfigurationError('adapter_factory_invalid')
    if adapter.capabilities.credential_mode != provider.credential_mode:
        raise ProviderConfigurationError('adapter_credential_mode_mismatch')
    if provider.per_user_lifecycle and not adapter.capabilities.supports_full_lifecycle:
        raise ProviderConfigurationError('adapter_lifecycle_incomplete')
    if provider.usage_tracking and not adapter.capabilities.supports_usage:
        raise ProviderConfigurationError('adapter_usage_tracking_incomplete')
    return adapter
