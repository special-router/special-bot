from django.contrib import admin
from django.utils.html import format_html

from .models import UserVPN


@admin.register(UserVPN)
class UserVPNAdmin(admin.ModelAdmin):
    # UUID клиента в списке не показывается: список открывается чаще карточки и
    # переживает и скриншоты, и демонстрацию экрана. Искать по нему по-прежнему
    # можно — `search_fields` принимает его целиком.
    list_display = ['user_info', 'server_name', 'enabled_status', 'created_at']
    list_filter = ['enabled', 'server', 'created_at']
    search_fields = ['user__username', 'user__telegram_id', 'server__name', 'vpn_uuid']
    readonly_fields = ['vpn_uuid', 'created_at', 'updated_at']

    fieldsets = (
        ('Основная информация', {'fields': ('user', 'server', 'enabled')}),
        ('VPN данные', {'fields': ('vpn_key', 'vpn_uuid')}),
        ('Системная информация', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )

    def user_info(self, obj):
        """Информация о пользователе"""
        username = obj.user.username or 'Без имени'
        return f"{username} (ID: {obj.user.telegram_id})"

    user_info.short_description = 'Пользователь'

    def server_name(self, obj):
        """Название сервера"""
        return obj.server.name

    server_name.short_description = 'Сервер'

    def enabled_status(self, obj):
        """Статус активности"""
        if obj.enabled:
            return format_html('<span style="color: green; font-weight: bold;">✓ Активно</span>')
        else:
            return format_html('<span style="color: red; font-weight: bold;">✗ Отключено</span>')

    enabled_status.short_description = 'Статус'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('user', 'server')
