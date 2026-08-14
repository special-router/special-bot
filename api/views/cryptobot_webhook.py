import json
import logging

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.payments.cryptobot_client import verify_webhook_signature
from apps.payments.models import CryptoBotInvoice, Transaction


logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def cryptobot_webhook(request):
    body = request.body
    signature = request.headers.get('Crypto-Pay-Api-Signature', '')

    if not verify_webhook_signature(settings.CRYPTOBOT_TOKEN, body, signature):
        return JsonResponse({'ok': False}, status=401)

    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'ok': False}, status=400)

    if data.get('update_type') != 'invoice_paid':
        return JsonResponse({'ok': True})

    payload = data.get('payload', {})
    invoice_id = payload.get('invoice_id')

    try:
        invoice = CryptoBotInvoice.objects.select_related('user').get(invoice_id=invoice_id)
    except CryptoBotInvoice.DoesNotExist:
        return JsonResponse({'ok': True})

    # Atomic: flip paid False→True; zero rows = already processed (retry or dup).
    claimed = CryptoBotInvoice.objects.filter(id=invoice.id, paid=False).update(paid=True)
    if not claimed:
        return JsonResponse({'ok': True})

    Transaction.objects.create(
        user=invoice.user,
        amount=invoice.amount_rub,
        status=TransactionStatusChoices.SUCCESS,
        source=TransactionSourceChoices.CRYPTO,
    )

    logger.info(
        'cryptobot_webhook invoice_id=%s user_id=%s amount_rub=%s',
        invoice_id,
        invoice.user_id,
        str(invoice.amount_rub),
    )

    return JsonResponse({'ok': True})
