from django.contrib import admin, messages
from django.utils.html import format_html
from django import forms
from django.core.exceptions import ValidationError

from .models import Broadcast
from apps.users.models import TelegramUser


class BroadcastForm(forms.ModelForm):
    """Форма для создания и редактирования рассылок"""
    
    class Meta:
        model = Broadcast
        fields = ['title', 'message', 'scheduled_at']
        widgets = {
            'message': forms.Textarea(attrs={'rows': 10, 'cols': 80}),
            'scheduled_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # self.fields['title'].help_text = 'Краткое описание рассылки'
        # self.fields['message'].help_text = 'Текст сообщения для рассылки'
        # self.fields['scheduled_at'].help_text = 'Время для отложенной отправки (оставьте пустым для немедленной отправки)'
    
    def clean_message(self):
        message = self.cleaned_data.get('message')
        if not message or len(message.strip()) < 10:
            raise ValidationError('Сообщение должно содержать минимум 10 символов')
        return message.strip()


@admin.register(Broadcast)
class BroadcastAdmin(admin.ModelAdmin):
    form = BroadcastForm
    
    list_display = [
        'title', 
        'status_badge', 
        'total_users', 
        'sent_count', 
        'failed_count', 
        'success_rate_display',
        'created_by', 
        'created_at',
        'sent_at'
    ]
    
    list_filter = [
        'status',
        'created_at',
        'sent_at',
        'created_by',
    ]
    
    search_fields = [
        'title',
        'message',
        'created_by__username',
    ]
    
    readonly_fields = [
        'status',
        'total_users',
        'sent_count', 
        'failed_count',
        'error_message',
        'created_by',
        'created_at',
        'updated_at',
        'sent_at',
        'success_rate_display',
        'preview_message',
    ]
    
    fieldsets = (
        ('Основная информация', {
            'fields': ('title', 'message', 'preview_message')
        }),
        ('Настройки отправки', {
            'fields': ('scheduled_at',)
        }),
        ('Статистика', {
            'fields': (
                'status',
                'total_users',
                'sent_count',
                'failed_count', 
                'success_rate_display',
                'error_message'
            ),
            'classes': ('collapse',)
        }),
        ('Системная информация', {
            'fields': (
                'created_by',
                'created_at',
                'updated_at',
                'sent_at'
            ),
            'classes': ('collapse',)
        }),
    )
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('created_by')
    
    def save_model(self, request, obj, form, change):
        if not change:  # Создание новой рассылки
            obj.created_by = request.user
            obj.total_users = TelegramUser.objects.count()
        super().save_model(request, obj, form, change)
    
    def status_badge(self, obj):
        """Отображает статус с цветным бейджем"""
        colors = {
            'draft': 'gray',
            'sending': 'orange', 
            'sent': 'green',
            'failed': 'red'
        }
        color = colors.get(obj.status, 'gray')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 8px; border-radius: 3px; font-size: 11px;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = 'Статус'
    
    def success_rate_display(self, obj):
        """Отображает процент успешных отправок"""
        if obj.total_users == 0:
            return '0%'
        rate = obj.success_rate
        color = 'green' if rate >= 90 else 'orange' if rate >= 70 else 'red'
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}%</span>',
            color,
            rate
        )
    success_rate_display.short_description = 'Успешность'
    
    def preview_message(self, obj):
        """Показывает превью сообщения"""
        if not obj.message:
            return 'Сообщение не задано'
        
        # Ограничиваем длину превью
        preview = obj.message[:200]
        if len(obj.message) > 200:
            preview += '...'
        
        return format_html(
            '<div style="background-color: #f8f9fa; padding: 10px; border-radius: 5px; border-left: 4px solid #007bff; max-width: 600px;">{}</div>',
            preview.replace('\n', '<br>')
        )
    preview_message.short_description = 'Превью сообщения'
    
    def has_add_permission(self, request):
        return True
    
    def has_change_permission(self, request, obj=None):
        if obj and obj.is_sending():
            return False  # Нельзя редактировать во время отправки
        return True
    
    def has_delete_permission(self, request, obj=None):
        if obj and obj.is_sending():
            return False  # Нельзя удалять во время отправки
        return True
    
    def get_readonly_fields(self, request, obj=None):
        readonly = list(self.readonly_fields)
        if obj and obj.is_completed():
            # Если рассылка завершена, делаем все поля только для чтения
            readonly.extend(['title', 'message', 'scheduled_at'])
        return readonly
    
    actions = ['send_broadcast', 'duplicate_broadcast']
    
    def send_broadcast(self, request, queryset):
        """Действие для отправки рассылки"""
        from .tasks import send_broadcast_task
        
        sent_count = 0
        for broadcast in queryset:
            if broadcast.can_be_sent():
                # Запускаем задачу отправки
                #send_broadcast_task.delay(broadcast.id)
                send_broadcast_task(broadcast.id)
                sent_count += 1
            else:
                self.message_user(
                    request,
                    f'Рассылка "{broadcast.title}" не может быть отправлена (статус: {broadcast.get_status_display()})',
                    level=messages.WARNING
                )
        
        if sent_count > 0:
            self.message_user(
                request,
                f'Запущена отправка {sent_count} рассылок',
                level=messages.SUCCESS
            )
    send_broadcast.short_description = 'Отправить выбранные рассылки'
    
    def duplicate_broadcast(self, request, queryset):
        """Действие для дублирования рассылки"""
        duplicated_count = 0
        for broadcast in queryset:
            new_broadcast = Broadcast.objects.create(
                title=f'{broadcast.title} (копия)',
                message=broadcast.message,
                created_by=request.user,
                total_users=TelegramUser.objects.count(),
                status='draft'
            )
            duplicated_count += 1
        
        self.message_user(
            request,
            f'Создано {duplicated_count} копий рассылок',
            level=messages.SUCCESS
        )
    duplicate_broadcast.short_description = 'Дублировать выбранные рассылки'
