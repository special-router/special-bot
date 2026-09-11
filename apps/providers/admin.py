from django.contrib import admin

from apps.providers.models import Provider, ProviderInventoryVersion


@admin.register(Provider)
class ProviderAdmin(admin.ModelAdmin):
    list_display = (
        'slug',
        'adapter',
        'credential_mode',
        'enabled',
        'production_ready',
        'priority',
    )
    list_filter = ('adapter', 'credential_mode', 'enabled')
    search_fields = ('slug', 'name')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(ProviderInventoryVersion)
class ProviderInventoryVersionAdmin(admin.ModelAdmin):
    list_display = ('provider', 'revision', 'state', 'endpoint_count', 'fetched_at', 'published_at')
    list_filter = ('provider', 'state')
    readonly_fields = (
        'provider',
        'revision',
        'source_digest',
        'payload_digest',
        'state',
        'endpoint_count',
        'fetched_at',
        'validated_at',
        'published_at',
        'expires_at',
        'error_class',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
