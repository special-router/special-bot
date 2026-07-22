import asyncio
import json
import logging

from asgiref.sync import async_to_sync
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from telegram import Bot

from apps.payments.choices import (
    InvoiceStatusChoices,
    PaymentMethodChoices,
    TransactionSourceChoices,
)
from apps.payments.cryptobot.client import CryptoBotClient
from apps.payments.models import Invoice
from apps.payments.services import credit_top_up_sync
from apps.subscriptions.payment_handlers import handle_router_payment_payload

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def cryptobot_webhook(request):
    signature = request.headers.get('crypto-pay-api-signature', '')
    body = request.body

    if not settings.CRYPTOBOT_TOKEN:
        return JsonResponse({'ok': False}, status=503)

    if not CryptoBotClient.verify_signature(body, signature, settings.CRYPTOBOT_TOKEN):
        return JsonResponse({'ok': False}, status=403)

    try:
        webhook_data = json.loads(body)
    except json.JSONDecodeError:
        return JsonResponse({'ok': False}, status=400)

    if webhook_data.get('update_type') != 'invoice_paid':
        return HttpResponse('ok')

    invoice_data = webhook_data.get('payload', {})
    external_id = str(invoice_data.get('invoice_id', ''))

    invoice = Invoice.objects.filter(external_id=external_id).select_related('user').first()
    if not invoice or invoice.status == InvoiceStatusChoices.PAID:
        return HttpResponse('ok')

    payload = invoice.payload or {}

    if payload.get('type') == 'router':
        async_to_sync(_process_router_cryptobot)(invoice, payload)
    else:
        product_line = payload.get('product_line', invoice.product_line)
        amount_rub = float(invoice.amount)
        credit_top_up_sync(
            user=invoice.user,
            amount=amount_rub,
            product_line=product_line,
            source=TransactionSourceChoices.CRYPTOBOT,
            payment_method=PaymentMethodChoices.CRYPTOBOT,
            invoice=invoice,
        )
        asyncio.run(_notify_balance_topup(invoice.user.telegram_id, amount_rub, product_line))

    invoice.status = InvoiceStatusChoices.PAID
    invoice.save(update_fields=['status'])

    return HttpResponse('ok')


async def _process_router_cryptobot(invoice: Invoice, payload: dict) -> None:
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)

    class BotContext:
        def __init__(self, telegram_bot):
            self.bot = telegram_bot

    await handle_router_payment_payload(payload, invoice.user, None, BotContext(bot))


async def _notify_balance_topup(telegram_id: int, amount: float, product_line: str) -> None:
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    try:
        await bot.send_message(
            chat_id=telegram_id,
            text=f'Оплата через CryptoBot получена: {amount} руб. зачислено на баланс ({product_line}).',
        )
    except Exception:
        logger.exception('Failed to notify user %s about CryptoBot payment', telegram_id)
