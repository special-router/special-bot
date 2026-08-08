from django.contrib import admin

from .models import MonitorState, MonitorTransition


@admin.register(MonitorState)
class MonitorStateAdmin(admin.ModelAdmin):
    list_display = ('layer', 'last_ok', 'alert', 'consecutive_failures', 'error_class', 'checked_at')
    readonly_fields = (
        'layer',
        'last_ok',
        'alert',
        'consecutive_failures',
        'error_class',
        'details',
        'checked_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MonitorTransition)
class MonitorTransitionAdmin(admin.ModelAdmin):
    list_display = ('layer', 'event', 'consecutive_failures', 'error_class', 'created_at')
    readonly_fields = ('layer', 'event', 'consecutive_failures', 'error_class', 'created_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
