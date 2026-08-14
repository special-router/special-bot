from datetime import timedelta

from django import forms
from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.template.response import TemplateResponse
from django.utils import timezone
from django.utils.html import format_html

from . import broadcast_ops
from .models import Broadcast, BroadcastDelivery


class BroadcastForm(forms.ModelForm):
    """Form for safe, auditable broadcasts."""

    class Meta:
        model = Broadcast
        fields = ['title', 'message', 'audience', 'include_subscription_button']
        widgets = {'message': forms.Textarea(attrs={'rows': 10, 'cols': 80})}

    def clean_message(self):
        message = self.cleaned_data.get('message', '').strip()
        if len(message) < broadcast_ops.MESSAGE_MIN_LENGTH:
            raise ValidationError(f'Сообщение должно содержать минимум {broadcast_ops.MESSAGE_MIN_LENGTH} символов')
        if len(message) > broadcast_ops.MESSAGE_MAX_LENGTH:
            raise ValidationError(f'Сообщение не должно превышать {broadcast_ops.MESSAGE_MAX_LENGTH} символов')
        return message


@admin.register(Broadcast)
class BroadcastAdmin(admin.ModelAdmin):
    form = BroadcastForm
    list_display = [
        'title', 'audience', 'status_badge', 'total_users', 'sent_count', 'failed_count',
        'success_rate_display', 'created_by', 'created_at', 'sent_at',
    ]
    list_filter = ['status', 'audience', 'created_at', 'sent_at', 'created_by']
    search_fields = ['title', 'message', 'created_by__username']
    readonly_fields = [
        'status', 'total_users', 'sent_count', 'failed_count', 'error_message', 'created_by',
        'created_at', 'updated_at', 'preview_snapshot_id', 'heartbeat_at', 'sent_at', 'success_rate_display', 'preview_message', 'audience_count',
    ]
    fieldsets = (
        ('Основная информация', {'fields': ('title', 'message', 'preview_message')}),
        ('Аудитория и кнопка', {'fields': ('audience', 'audience_count', 'include_subscription_button')}),
        ('Статистика', {
            'fields': ('status', 'total_users', 'sent_count', 'failed_count', 'success_rate_display', 'error_message'),
            'classes': ('collapse',),
        }),
        ('Системная информация', {'fields': ('created_by', 'created_at', 'updated_at', 'preview_snapshot_id', 'heartbeat_at', 'sent_at'), 'classes': ('collapse',)}),
    )
    actions = ['send_broadcast', 'resume_broadcast', 'duplicate_broadcast', 'recover_stale_broadcast']

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('created_by')

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
            obj.total_users = obj.recipient_queryset().count()
            super().save_model(request, obj, form, change)
            return
        editable = {field: getattr(obj, field) for field in ('title', 'message', 'audience', 'include_subscription_button')}
        with transaction.atomic():
            updated = Broadcast.objects.filter(pk=obj.pk, status='draft').update(**editable)
            if updated:
                BroadcastDelivery.objects.filter(broadcast_id=obj.pk).delete()
        if not updated:
            raise ValidationError('Рассылка больше не является черновиком; создайте копию.')

    def status_badge(self, obj):
        colors = {
            'draft': 'gray',
            'confirming': 'purple',
            'queued': 'blue',
            'sending': 'orange',
            'sent': 'green',
            'failed': 'red',
        }
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 8px; border-radius: 3px; font-size: 11px;">{}</span>',
            colors.get(obj.status, 'gray'), obj.get_status_display(),
        )
    status_badge.short_description = 'Статус'

    def audience_count(self, obj):
        if not obj.pk:
            return 'Будет рассчитано после сохранения'
        if obj.deliveries.exists():
            return obj.deliveries.count()
        return obj.recipient_queryset().count()
    audience_count.short_description = 'Получателей в выбранной аудитории'

    def success_rate_display(self, obj):
        if obj.total_users == 0:
            return '0%'
        rate = obj.success_rate
        color = 'green' if rate >= 90 else 'orange' if rate >= 70 else 'red'
        return format_html('<span style="color: {}; font-weight: bold;">{}%</span>', color, rate)
    success_rate_display.short_description = 'Успешность'

    def preview_message(self, obj):
        if not obj.message:
            return 'Сообщение не задано'
        preview = obj.message[:200] + ('...' if len(obj.message) > 200 else '')
        return format_html(
            '<div style="white-space: pre-wrap; background-color: #f8f9fa; padding: 10px; border-radius: 5px; border-left: 4px solid #007bff; max-width: 600px;">{}</div>',
            preview,
        )
    preview_message.short_description = 'Превью сообщения'

    def has_change_permission(self, request, obj=None):
        if not super().has_change_permission(request, obj):
            return False
        return obj is None or obj.status == 'draft'

    def has_delete_permission(self, request, obj=None):
        if not super().has_delete_permission(request, obj):
            return False
        return obj is None or obj.status == 'draft'

    def get_readonly_fields(self, request, obj=None):
        readonly = list(self.readonly_fields)
        if obj and obj.status != 'draft':
            readonly.extend(['title', 'message', 'audience', 'include_subscription_button'])
        return readonly

    def _require_change_permission(self, request):
        if not self.has_change_permission(request):
            raise PermissionDenied

    def _single_selected_broadcast(self, request, queryset):
        selected = list(queryset[:2])
        if len(selected) != 1:
            self.message_user(request, 'Выберите ровно одну рассылку.', level=messages.ERROR)
            return None
        return selected[0]

    @staticmethod
    def _confirmation_digest(broadcast):
        """Bind confirmation to content, audience controls, and immutable recipient snapshot."""
        return broadcast_ops.confirmation_digest(broadcast)

    def _create_preview_snapshot(self, broadcast_id):
        """Atomically replace any old preview with a fixed recipient ledger."""
        return broadcast_ops.create_preview_snapshot(broadcast_id)

    def _confirmation(self, request, broadcast, action, heading, warning):
        context = {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'action_checkbox_name': helpers.ACTION_CHECKBOX_NAME,
            'action': action,
            'broadcast': broadcast,
            'recipient_count': broadcast.total_users,
            'confirmation_digest': self._confirmation_digest(broadcast),
            'heading': heading,
            'warning': warning,
        }
        return TemplateResponse(request, 'admin/telegram_bot/broadcast/confirm_action.html', context)

    def _confirmation_is_current(self, request, broadcast):
        return request.POST.get('confirmation_digest', '') == self._confirmation_digest(broadcast)

    def _enqueue_after_commit(self, broadcast_id):
        broadcast_ops.enqueue_after_commit(broadcast_id)

    @admin.action(description='Поставить рассылку в очередь')
    def send_broadcast(self, request, queryset):
        self._require_change_permission(request)
        selected = self._single_selected_broadcast(request, queryset)
        if not selected:
            return
        if request.POST.get('cancel'):
            broadcast_ops.cancel_confirming_broadcast(selected.pk)
            self.message_user(request, 'Подтверждение отменено; снимок получателей удалён.', level=messages.SUCCESS)
            return
        if 'post' not in request.POST:
            if selected.status != 'draft':
                self.message_user(request, 'В очередь можно поставить только черновик.', level=messages.ERROR)
                return
            broadcast = self._create_preview_snapshot(selected.pk)
            if not broadcast:
                self.message_user(request, 'Рассылка изменилась; создайте подтверждение заново.', level=messages.ERROR)
                return
            return self._confirmation(
                request, broadcast, 'send_broadcast', 'Подтвердите отправку',
                'Получатели уже зафиксированы в снимке. Неопределённые доставки не повторяются.',
            )
        digest = request.POST.get('confirmation_digest', '')
        result = broadcast_ops.queue_confirmed_broadcast(selected.pk, digest)
        if result.error == 'stale':
            self.message_user(request, 'Рассылка или снимок получателей изменились; создайте подтверждение заново.', level=messages.ERROR)
            return
        if result.error == 'snapshot_corrupt':
            self.message_user(request, 'Снимок получателей повреждён; создайте подтверждение заново.', level=messages.ERROR)
            return
        self.message_user(request, 'Зафиксированная рассылка поставлена в очередь.', level=messages.SUCCESS)

    @admin.action(description='Возобновить ожидающие доставки')
    def resume_broadcast(self, request, queryset):
        self._require_change_permission(request)
        selected = self._single_selected_broadcast(request, queryset)
        if not selected:
            return
        if 'post' not in request.POST:
            if selected.status != 'failed' or not selected.deliveries.filter(status=BroadcastDelivery.STATUS_PENDING).exists():
                self.message_user(request, 'Возобновить можно только ошибочную рассылку с ожидающими доставками.', level=messages.ERROR)
                return
            return self._confirmation(
                request, selected, 'resume_broadcast', 'Подтвердите возобновление',
                'Будут поставлены в очередь только ожидающие доставки; отправленные, ошибочные и неопределённые не изменятся.',
            )
        with transaction.atomic():
            broadcast = Broadcast.objects.select_for_update().filter(pk=selected.pk, status='failed').first()
            if not broadcast or not self._confirmation_is_current(request, broadcast):
                self.message_user(request, 'Рассылка изменилась; подтвердите актуальную версию заново.', level=messages.ERROR)
                return
            if not broadcast.deliveries.filter(status=BroadcastDelivery.STATUS_PENDING).exists():
                self.message_user(request, 'Ожидающих доставок больше нет.', level=messages.ERROR)
                return
            broadcast.status = 'queued'
            broadcast.error_message = ''
            broadcast.heartbeat_at = None
            broadcast.save(update_fields=['status', 'error_message', 'heartbeat_at', 'updated_at'])
            self._enqueue_after_commit(broadcast.id)
        self.message_user(request, 'Ожидающие доставки поставлены в очередь.', level=messages.SUCCESS)

    @admin.action(description='Дублировать выбранные рассылки')
    def duplicate_broadcast(self, request, queryset):
        self._require_change_permission(request)
        if not self.has_add_permission(request):
            raise PermissionDenied
        for broadcast in queryset:
            Broadcast.objects.create(
                title=f'{broadcast.title} (копия)', message=broadcast.message, created_by=request.user,
                audience=broadcast.audience, include_subscription_button=broadcast.include_subscription_button,
                total_users=broadcast.recipient_queryset().count(), status='draft',
            )
        self.message_user(request, f'Создано копий: {queryset.count()}', level=messages.SUCCESS)

    @admin.action(description='Завершить устаревшую прерванную рассылку')
    def recover_stale_broadcast(self, request, queryset):
        self._require_change_permission(request)
        selected = self._single_selected_broadcast(request, queryset)
        if not selected:
            return
        stale_before = timezone.now() - timedelta(minutes=30)
        is_stale = selected.status == 'sending' and (
            selected.heartbeat_at is None or selected.heartbeat_at <= stale_before
        )
        if not is_stale:
            self.message_user(request, 'Доступны только рассылки без активности доставки более 30 минут.', level=messages.ERROR)
            return
        if 'post' not in request.POST:
            return self._confirmation(
                request, selected, 'recover_stale_broadcast', 'Подтвердите завершение',
                'Статус станет «ошибка». Реестр доставок сохранится; неопределённые доставки не будут повторены.',
            )
        with transaction.atomic():
            broadcast = Broadcast.objects.select_for_update().filter(pk=selected.pk, status='sending').first()
            if not broadcast or not self._confirmation_is_current(request, broadcast):
                self.message_user(request, 'Рассылка изменилась; подтвердите актуальную версию заново.', level=messages.ERROR)
                return
            if broadcast.heartbeat_at is not None and broadcast.heartbeat_at > stale_before:
                self.message_user(request, 'Задача доставки снова активна.', level=messages.ERROR)
                return
            broadcast.status = 'failed'
            broadcast.error_message = 'Задача доставки остановлена; реестр сохранён, неопределённые доставки не повторяются'
            broadcast.save(update_fields=['status', 'error_message', 'updated_at'])
        self.message_user(request, 'Устаревшая рассылка помечена ошибочной; реестр доставок сохранён.', level=messages.SUCCESS)
