import asyncio
import logging
import math
import time

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut

from .models import Broadcast, BroadcastDelivery


logger = logging.getLogger(__name__)
PACING_SECONDS = 1


def _error_class(error):
    """Persist/log only a safe exception category, never a Telegram response."""
    return error.__class__.__name__[:64]


def _reply_markup(broadcast):
    if not broadcast.include_subscription_button:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton('🔑 Открыть мою подписку', callback_data='show_keys')]]
    )


def _refresh_totals(broadcast, *, complete=False):
    totals = broadcast.deliveries.values('status').annotate(count=Count('id'))
    counts = {row['status']: row['count'] for row in totals}
    broadcast.total_users = sum(counts.values())
    broadcast.sent_count = counts.get(BroadcastDelivery.STATUS_SENT, 0)
    broadcast.failed_count = counts.get(BroadcastDelivery.STATUS_FAILED, 0)
    pending_count = counts.get(BroadcastDelivery.STATUS_PENDING, 0)
    uncertain_count = counts.get(BroadcastDelivery.STATUS_SENDING, 0)
    errors = []
    if broadcast.failed_count:
        errors.append(f'Ошибки доставки: {broadcast.failed_count}')
    if pending_count:
        errors.append(f'Ожидающих доставок: {pending_count}')
    if uncertain_count:
        errors.append(f'Неопределённых доставок: {uncertain_count}')
    broadcast.error_message = '; '.join(errors)
    if complete:
        broadcast.status = 'sent' if not broadcast.failed_count and not pending_count and not uncertain_count else 'failed'
        broadcast.sent_at = timezone.now()
    broadcast.save(
        update_fields=['total_users', 'sent_count', 'failed_count', 'error_message', 'status', 'sent_at', 'updated_at']
    )


def _touch_heartbeat(broadcast_id):
    """Only an active owner task may renew its lease."""
    now = timezone.now()
    Broadcast.objects.filter(pk=broadcast_id, status='sending').update(
        heartbeat_at=now, updated_at=now
    )


def _claim_broadcast(broadcast_id):
    """A task may claim only a confirmation-created queue entry, never a draft."""
    with transaction.atomic():
        broadcast = Broadcast.objects.select_for_update().filter(
            pk=broadcast_id, status='queued'
        ).first()
        if not broadcast:
            return None
        now = timezone.now()
        broadcast.status = 'sending'
        broadcast.error_message = ''
        broadcast.heartbeat_at = now
        broadcast.save(update_fields=['status', 'error_message', 'heartbeat_at', 'updated_at'])
        return broadcast


def _claim_delivery(broadcast_id):
    """Claim pending rows only while the parent task still owns the broadcast."""
    with transaction.atomic():
        broadcast = Broadcast.objects.select_for_update().filter(
            pk=broadcast_id, status='sending'
        ).first()
        if not broadcast:
            return None
        delivery = (
            BroadcastDelivery.objects.select_for_update()
            .select_related('user')
            .filter(broadcast_id=broadcast_id, status=BroadcastDelivery.STATUS_PENDING)
            .order_by('id')
            .first()
        )
        if not delivery:
            return None
        now = timezone.now()
        delivery.status = BroadcastDelivery.STATUS_SENDING
        delivery.error_class = ''
        delivery.attempt_count += 1
        delivery.sending_at = now
        delivery.save(update_fields=['status', 'error_class', 'attempt_count', 'sending_at', 'updated_at'])
        broadcast.heartbeat_at = now
        broadcast.save(update_fields=['heartbeat_at', 'updated_at'])
        return delivery


def _delivery_lease_active(delivery_id):
    """Atomically verify the delivery is still owned immediately before I/O."""
    with transaction.atomic():
        return BroadcastDelivery.objects.select_for_update().filter(
            pk=delivery_id,
            status=BroadcastDelivery.STATUS_SENDING,
            broadcast__status='sending',
        ).exists()


def _finish_delivery(delivery_id, *, error=None):
    """Do not alter a row after stale recovery has revoked the parent lease."""
    with transaction.atomic():
        delivery = (
            BroadcastDelivery.objects.select_for_update()
            .select_related('broadcast')
            .filter(pk=delivery_id, broadcast__status='sending')
            .first()
        )
        if not delivery:
            return
        now = timezone.now()
        if error is None:
            delivery.status = BroadcastDelivery.STATUS_SENT
            delivery.sent_at = now
            delivery.error_class = ''
            delivery.save(update_fields=['status', 'sent_at', 'error_class', 'updated_at'])
        else:
            delivery.status = BroadcastDelivery.STATUS_FAILED
            delivery.error_class = _error_class(error)
            delivery.save(update_fields=['status', 'error_class', 'updated_at'])
        Broadcast.objects.filter(pk=delivery.broadcast_id, status='sending').update(
            heartbeat_at=now, updated_at=now
        )


def _finalize_task_failure(broadcast_id):
    """Make a stopped task visible while retaining every ledger row unchanged."""
    with transaction.atomic():
        broadcast = Broadcast.objects.select_for_update().filter(pk=broadcast_id, status='sending').first()
        if not broadcast:
            return
        _refresh_totals(broadcast)
        broadcast.status = 'failed'
        broadcast.error_message = 'Задача доставки прервана' + (
            f'; {broadcast.error_message}' if broadcast.error_message else ''
        )
        broadcast.save(update_fields=['status', 'error_message', 'updated_at'])


def _retry_after_seconds(error):
    retry_after = error.retry_after
    seconds = retry_after.total_seconds() if hasattr(retry_after, 'total_seconds') else retry_after
    return max(math.ceil(float(seconds)), 1)


@shared_task(name='apps.telegram_bot.tasks.safe_broadcast_v1')
def safe_broadcast_v1(broadcast_id):
    """Send one confirmed snapshot on the quarantined versioned queue.

    Unknown/network outcomes remain ``sending``: retrying them could duplicate a
    Telegram message.  Initialization, shutdown, and task errors are converted to
    safe class-only logs and a failed parent state, never re-raised to Celery.
    """
    broadcast = _claim_broadcast(broadcast_id)
    if broadcast is None:
        logger.warning('Broadcast task ignored: not confirmation-queued')
        return

    loop = asyncio.new_event_loop()
    bot = None
    markup = _reply_markup(broadcast)
    try:
        asyncio.set_event_loop(loop)
        bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
        loop.run_until_complete(bot.initialize())
        _touch_heartbeat(broadcast.id)
        while delivery := _claim_delivery(broadcast.id):
            while True:
                # Recovery can revoke the parent lease while RetryAfter sleeps.
                # Never make another external call after that revocation.
                if not _delivery_lease_active(delivery.id):
                    logger.warning('Broadcast delivery lease revoked before network call')
                    return
                try:
                    loop.run_until_complete(
                        bot.send_message(
                            chat_id=delivery.user.telegram_id,
                            text=broadcast.message,
                            reply_markup=markup,
                        )
                    )
                except RetryAfter as error:
                    logger.warning('Telegram rate limit encountered; pausing delivery')
                    _touch_heartbeat(broadcast.id)
                    time.sleep(_retry_after_seconds(error))
                    continue
                except (Forbidden, BadRequest) as error:
                    _finish_delivery(delivery.id, error=error)
                    logger.warning('Telegram delivery permanently failed: %s', _error_class(error))
                except (NetworkError, TimedOut) as error:
                    logger.warning('Telegram delivery outcome uncertain: %s', _error_class(error))
                    _touch_heartbeat(broadcast.id)
                except Exception as error:
                    logger.warning('Telegram delivery outcome uncertain: %s', _error_class(error))
                    _touch_heartbeat(broadcast.id)
                else:
                    _finish_delivery(delivery.id)
                break
            time.sleep(PACING_SECONDS)

        with transaction.atomic():
            final_broadcast = Broadcast.objects.select_for_update().filter(pk=broadcast.id, status='sending').first()
            if final_broadcast:
                _refresh_totals(final_broadcast, complete=True)
                logger.info('Broadcast completed: sent=%s failed=%s', final_broadcast.sent_count, final_broadcast.failed_count)
    except Exception as error:
        _finalize_task_failure(broadcast.id)
        logger.error('Broadcast task interrupted: %s', _error_class(error))
    finally:
        if bot is not None:
            try:
                loop.run_until_complete(bot.shutdown())
            except Exception as error:
                _finalize_task_failure(broadcast.id)
                logger.warning('Telegram bot shutdown failed: %s', _error_class(error))
        asyncio.set_event_loop(None)
        loop.close()
