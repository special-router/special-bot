from django.contrib import admin
from .models import TelegramUser


@admin.register(TelegramUser)
class TelegramUserAdmin(admin.ModelAdmin):
    list_display = ['telegram_id', 'username', 'is_active_promo', 'created_at']
    list_filter = ['is_active_promo', 'created_at']
    search_fields = ['telegram_id', 'username']
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('Основная информация', {
            'fields': ('telegram_id', 'username')
        }),
        ('Реферальная система', {
            'fields': ('referral_user', 'is_active_promo')
        }),
        ('Системная информация', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
