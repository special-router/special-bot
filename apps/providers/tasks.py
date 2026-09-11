from __future__ import annotations

from celery import shared_task

from apps.providers.adapters import ProviderConfigurationError, ProviderTemporaryError, adapter_for_provider
from apps.providers.models import Provider
from apps.providers.services.inventory import stage_inventory


@shared_task(bind=True, name='apps.providers.tasks.refresh_provider_inventory', max_retries=5)
def refresh_provider_inventory(self, provider_id: int) -> dict[str, object]:
    provider = Provider.objects.get(pk=provider_id)
    if not provider.enabled:
        return {'provider_id': provider_id, 'skipped': True, 'reason': 'disabled'}
    try:
        adapter = adapter_for_provider(provider)
        result = adapter.fetch_inventory()
    except ProviderTemporaryError:
        safe_error = ProviderTemporaryError('provider_temporary_failure')
        raise self.retry(exc=safe_error, countdown=min(300, 2 ** self.request.retries * 15)) from None
    except ProviderConfigurationError:
        raise ProviderConfigurationError('provider_configuration_failure') from None
    except Exception:
        raise ProviderConfigurationError('provider_adapter_failure') from None
    inventory = stage_inventory(
        provider_id=provider.id,
        source_digest=result.source_digest,
        payload_digest=result.payload_digest,
        endpoints=result.endpoints,
        fetched_at=result.fetched_at,
        expires_at=result.expires_at,
    )
    return {
        'provider_id': provider.id,
        'inventory_id': inventory.id,
        'revision': inventory.revision,
        'endpoint_count': inventory.endpoint_count,
    }
