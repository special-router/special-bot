"""Опрос CryptoBot: единственный путь, которым оплата счёта становится балансом.

Вебхук провайдера в этом развёртывании не зарегистрирован — наружу смотрит только
подписочный домен, а бот работает на long polling. Поэтому об оплате узнаём
опросом: раз в минуту спрашиваем провайдера про счета, которые сами и выставили
и которые ещё не зачислены.

Спрашиваем поимённо, а не «покажи всё»: список ограничен нашими же неоплаченными
счетами за окно, так что нагрузка на провайдера не растёт вместе с историей
платежей. Само зачисление живёт в `credit_cryptobot_invoice` и идемпотентно, так
что повторный опрос до и после оплаты одинаково безопасен.
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime
import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone
from telegram import Bot

from apps.payments.bonus import topup_bonus_amount
from apps.payments.cryptobot_client import get_invoices_sync
from apps.payments.cryptobot_credit import credit_cryptobot_invoice
from apps.payments.models import CryptoBotInvoice


logger = logging.getLogger(__name__)

# Провайдер принимает список номеров одним запросом; сотня за раз с запасом
# покрывает любой реальный поток и держит URL в разумной длине.
_BATCH_SIZE = 100
# Счёт выставляется на час. Окно шире срока жизни, чтобы оплата, замеченная
# провайдером на границе, всё равно попала в опрос; всё, что старше, оплатить
# уже нельзя, и держать эти строки в опросе незачем.
_DEFAULT_WINDOW_HOURS = 24

CREDITED_TEXT = 'Оплата получена, баланс пополнен на {amount} ₽.'


@shared_task
def poll_cryptobot_invoices() -> int:
    """Зачислить все оплаченные счета из окна. Возвращает число зачисленных."""
    token = getattr(settings, 'CRYPTOBOT_TOKEN', '')
    if not token:
        return 0

    window_hours = getattr(settings, 'CRYPTOBOT_POLL_WINDOW_HOURS', _DEFAULT_WINDOW_HOURS)
    since = timezone.now() - datetime.timedelta(hours=window_hours)
    pending = list(
        CryptoBotInvoice.objects
        .select_related('user')
        .filter(paid=False, created_at__gte=since)
        .order_by('id')
    )
    if not pending:
        return 0

    by_number = {invoice.invoice_id: invoice for invoice in pending}
    credited = []
    for offset in range(0, len(pending), _BATCH_SIZE):
        numbers = [invoice.invoice_id for invoice in pending[offset:offset + _BATCH_SIZE]]
        items = get_invoices_sync(token, numbers)
        if items is None:
            # Провайдер недоступен: следующий прогон через минуту повторит тот же
            # запрос. Прерываем цикл, а не пропускаем пачку — при сетевой аварии
            # остальные пачки ответят тем же, и незачем стучаться ещё девять раз.
            break
        for item in items:
            if str(item.get('status')) != 'paid':
                continue
            invoice = by_number.get(_invoice_number(item))
            if invoice is None:
                continue
            try:
                if credit_cryptobot_invoice(invoice):
                    credited.append(invoice)
            except Exception:
                # Одна испорченная строка не должна оставить остальные оплаты
                # незачисленными; счёт уже помечен оплаченным только если
                # зачисление дошло до конца.
                logger.exception('cryptobot credit failed for invoice_id=%s', invoice.invoice_id)

    if credited:
        _notify_credited(credited)
    return len(credited)


def _invoice_number(item: dict) -> int | None:
    try:
        return int(item.get('invoice_id'))
    except (TypeError, ValueError):
        return None


def _notify_credited(invoices: list[CryptoBotInvoice]) -> None:
    """Сказать клиенту, что деньги дошли. Не имеет права уронить зачисление.

    Сообщение отправляется после того, как баланс уже пополнен, поэтому любая
    ошибка Telegram стоит клиенту уведомления, но не денег.
    """
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    if not token:
        return
    bot = Bot(token=token)
    for invoice in invoices:
        telegram_id = getattr(invoice.user, 'telegram_id', None)
        if not telegram_id:
            continue
        with contextlib.suppress(Exception):
            asyncio.run(bot.send_message(
                chat_id=telegram_id,
                # Названо зачисленное, а не уплаченное: клиент сверяет сообщение
                # с балансом, и объёмный бонус входит именно в баланс.
                text=CREDITED_TEXT.format(amount=int(topup_bonus_amount(invoice.amount_rub))),
            ))
