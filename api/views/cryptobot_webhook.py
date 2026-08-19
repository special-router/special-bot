import json
import logging

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.payments.cryptobot_client import verify_webhook_signature
from apps.payments.cryptobot_credit import credit_cryptobot_invoice
from apps.payments.models import CryptoBotInvoice


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

    # Зачисление и защита от повтора — общие с опросом (`poll_cryptobot_invoices`),
    # который в этом развёртывании и делает всю работу. Два пути могут прийти на
    # один счёт, и решает гонку атомарный переход внутри.
    credit_cryptobot_invoice(invoice)

    return JsonResponse({'ok': True})
