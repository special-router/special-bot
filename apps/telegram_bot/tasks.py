import time

from celery import shared_task
from django.conf import settings
from django.utils import timezone
from telegram import Bot
from telegram.error import TelegramError
import asyncio
import logging

from .models import Broadcast
from apps.users.models import TelegramUser

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def send_broadcast_task(self, broadcast_id):
    """
    Задача для отправки рассылки всем пользователям
    """
    try:
        broadcast = Broadcast.objects.get(id=broadcast_id)
        
        # Проверяем, можно ли отправить рассылку
        if not broadcast.can_be_sent():
            logger.warning(f'Broadcast {broadcast_id} cannot be sent (status: {broadcast.status})')
            return
        
        # Обновляем статус на "отправляется"
        broadcast.status = 'sending'
        broadcast.save()
        
        # Получаем всех активных пользователей
        users = TelegramUser.objects.all()
        total_users = users.count()
        
        if total_users == 0:
            broadcast.status = 'failed'
            broadcast.error_message = 'Нет пользователей для рассылки'
            broadcast.save()
            return
        
        # Обновляем общее количество пользователей
        broadcast.total_users = total_users
        broadcast.save()
        
        # Создаем бота
        bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
        
        # Отправляем сообщения
        sent_count = 0
        failed_count = 0
        error_messages = []
        
        for user in users:
            try:
                # Отправляем сообщение пользователю
                asyncio.run(bot.send_message(
                    chat_id=user.telegram_id,
                    text=broadcast.message,
                    parse_mode='HTML'
                ))
                sent_count += 1
                time.sleep(1)
                
                # Обновляем счетчики каждые 10 сообщений
                if (sent_count + failed_count) % 10 == 0:
                    broadcast.sent_count = sent_count
                    broadcast.failed_count = failed_count
                    broadcast.save()
                
            except TelegramError as e:
                failed_count += 1
                error_msg = f'User {user.telegram_id}: {str(e)}'
                error_messages.append(error_msg)
                logger.error(f'Failed to send message to user {user.telegram_id}: {e}')
                
                # Если ошибок слишком много, прерываем рассылку
                if failed_count > total_users * 0.5:  # Более 50% ошибок
                    broadcast.status = 'failed'
                    broadcast.error_message = f'Слишком много ошибок ({failed_count}/{total_users}). Остановка рассылки.'
                    broadcast.sent_count = sent_count
                    broadcast.failed_count = failed_count
                    broadcast.save()
                    return
        
        # Завершаем рассылку
        broadcast.status = 'sent' if failed_count == 0 else 'failed'
        broadcast.sent_count = sent_count
        broadcast.failed_count = failed_count
        broadcast.sent_at = timezone.now()
        
        if error_messages:
            broadcast.error_message = '\n'.join(error_messages[:10])  # Ограничиваем количество ошибок
            if len(error_messages) > 10:
                broadcast.error_message += f'\n... и еще {len(error_messages) - 10} ошибок'
        
        broadcast.save()
        
        logger.info(f'Broadcast {broadcast_id} completed. Sent: {sent_count}, Failed: {failed_count}')
        
    except Broadcast.DoesNotExist:
        logger.error(f'Broadcast {broadcast_id} not found')
    except Exception as e:
        logger.error(f'Error in send_broadcast_task: {e}')
        
        # Обновляем статус на ошибку
        try:
            broadcast = Broadcast.objects.get(id=broadcast_id)
            broadcast.status = 'failed'
            broadcast.error_message = str(e)
            broadcast.save()
        except:
            pass
        
        # Повторяем задачу через некоторое время
        raise self.retry(countdown=60, exc=e)


@shared_task
def send_scheduled_broadcasts():
    """
    Задача для отправки запланированных рассылок
    Запускается по расписанию
    """
    now = timezone.now()
    
    # Находим рассылки, которые нужно отправить
    scheduled_broadcasts = Broadcast.objects.filter(
        status='draft',
        scheduled_at__lte=now
    )
    
    for broadcast in scheduled_broadcasts:
        send_broadcast_task.delay(broadcast.id)
        logger.info(f'Scheduled broadcast {broadcast.id} queued for sending')


@shared_task
def cleanup_old_broadcasts():
    """
    Задача для очистки старых рассылок
    Удаляет рассылки старше 30 дней
    """
    from datetime import timedelta
    
    cutoff_date = timezone.now() - timedelta(days=30)
    
    old_broadcasts = Broadcast.objects.filter(
        created_at__lt=cutoff_date,
        status__in=['sent', 'failed']
    )
    
    count = old_broadcasts.count()
    old_broadcasts.delete()
    
    logger.info(f'Cleaned up {count} old broadcasts')
