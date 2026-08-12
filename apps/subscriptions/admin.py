from django.contrib import admin

from .models import SubscriptionDevice, SubscriptionDeviceReset


@admin.register(SubscriptionDevice)
class SubscriptionDeviceAdmin(admin.ModelAdmin):
    list_display = ['subscription', 'device_os', 'device_model', 'first_seen_at', 'last_seen_at']
    list_filter = ['device_os', 'first_seen_at']
    # Identifiers are deliberately absent from search and display: support
    # resolves devices through the owning subscription, never the raw hwid.
    search_fields = ['subscription__user__telegram_id', 'subscription__user__username']
    readonly_fields = ['hwid', 'first_seen_at', 'last_seen_at']

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('subscription__user')


@admin.register(SubscriptionDeviceReset)
class SubscriptionDeviceResetAdmin(admin.ModelAdmin):
    list_display = ['telegram_user', 'last_reset_at']
    search_fields = ['telegram_user__telegram_id', 'telegram_user__username']
