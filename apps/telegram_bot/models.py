from django.db import models
from django.contrib.auth.models import User


class Broadcast(models.Model):
    """Модель для рассылок всем пользователям"""
    
    STATUS_CHOICES = [
        ('draft', 'Черновик'),
        ('sending', 'Отправляется'),
        ('sent', 'Отправлено'),
        ('failed', 'Ошибка'),
    ]
    
    title = models.CharField(
        'Заголовок',
        max_length=200,
        help_text='Краткое описание рассылки'
    )
    
    message = models.TextField(
        'Сообщение',
        help_text='Текст сообщения для рассылки'
    )
    
    status = models.CharField(
        'Статус',
        max_length=20,
        choices=STATUS_CHOICES,
        default='draft'
    )
    
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        verbose_name='Создано пользователем',
        related_name='broadcasts'
    )
    
    total_users = models.PositiveIntegerField(
        'Всего пользователей',
        default=0,
        help_text='Общее количество пользователей для рассылки'
    )
    
    sent_count = models.PositiveIntegerField(
        'Отправлено',
        default=0,
        help_text='Количество успешно отправленных сообщений'
    )
    
    failed_count = models.PositiveIntegerField(
        'Ошибок',
        default=0,
        help_text='Количество неудачных отправок'
    )
    
    error_message = models.TextField(
        'Сообщение об ошибке',
        blank=True,
        null=True,
        help_text='Детали ошибки при отправке'
    )
    
    scheduled_at = models.DateTimeField(
        'Запланировано на',
        null=True,
        blank=True,
        help_text='Время для отложенной отправки (оставьте пустым для немедленной отправки)'
    )
    
    created_at = models.DateTimeField(
        'Создано',
        auto_now_add=True
    )
    
    updated_at = models.DateTimeField(
        'Обновлено',
        auto_now=True
    )
    
    sent_at = models.DateTimeField(
        'Отправлено',
        null=True,
        blank=True,
        help_text='Время фактической отправки'
    )
    
    photo = models.ImageField(
        'Фото',
        upload_to='broadcasts/',
        null=True,
        blank=True,
        help_text='Необязательно: изображение, которое будет отправлено вместе с текстом'
    )
    
    class Meta:
        verbose_name = 'Рассылка'
        verbose_name_plural = 'Рассылки'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"
    
    @property
    def success_rate(self):
        """Процент успешных отправок"""
        if self.total_users == 0:
            return 0
        return round((self.sent_count / self.total_users) * 100, 2)
    
    def can_be_sent(self):
        """Проверяет, можно ли отправить рассылку"""
        return self.status in ['draft', 'failed']
    
    def is_sending(self):
        """Проверяет, отправляется ли рассылка сейчас"""
        return self.status == 'sending'
    
    def is_completed(self):
        """Проверяет, завершена ли рассылка"""
        return self.status in ['sent', 'failed']
