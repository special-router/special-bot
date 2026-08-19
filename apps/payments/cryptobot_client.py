import hashlib
import hmac
import logging
from decimal import Decimal

import httpx


logger = logging.getLogger(__name__)

_CRYPTOBOT_BASE_URL = 'https://pay.crypt.bot/api/'


async def create_usdt_invoice(
    token: str,
    amount_usdt: str,
    user_db_id: int,
    description: str,
) -> dict | None:
    """Create a CryptoBot USDT invoice.

    Returns the result dict on success, None on any error. Errors are logged at
    WARNING and never raised — a payment provider failure must not propagate to
    the bot handler.
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f'{_CRYPTOBOT_BASE_URL}createInvoice',
                headers={'Crypto-Pay-API-Token': token},
                json={
                    'currency_type': 'crypto',
                    'asset': 'USDT',
                    'amount': amount_usdt,
                    'description': description,
                    'payload': str(user_db_id),
                    'expires_in': 3600,
                },
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
    except Exception:
        logger.warning('cryptobot create_invoice failed user_db_id=%s', user_db_id, exc_info=True)
        return None

    if not data.get('ok'):
        logger.warning(
            'cryptobot create_invoice not ok user_db_id=%s error=%s',
            user_db_id,
            data.get('error'),
        )
        return None

    return data.get('result')


async def get_usdt_rate(token: str) -> Decimal | None:
    """Fetch current RUB price of 1 USDT from CryptoBot.

    Returns None on any error — caller must handle gracefully.
    Never logs the token.
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f'{_CRYPTOBOT_BASE_URL}getExchangeRates',
                headers={'Crypto-Pay-API-Token': token},
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
    except Exception:
        logger.warning('cryptobot get_exchange_rates failed', exc_info=True)
        return None

    if not data.get('ok'):
        logger.warning('cryptobot get_exchange_rates not ok error=%s', data.get('error'))
        return None

    for entry in data.get('result', []):
        if (
            entry.get('source') == 'USDT'
            and entry.get('target') == 'RUB'
            and entry.get('is_valid')
        ):
            try:
                return Decimal(entry['rate'])
            except Exception:
                logger.warning('cryptobot get_exchange_rates bad rate value')
                return None

    logger.warning('cryptobot get_exchange_rates: USDT/RUB entry not found')
    return None


def get_invoices_sync(token: str, invoice_ids: list[int]) -> list[dict] | None:
    """Прочитать состояние конкретных счётов. Синхронно — вызывается из Celery.

    Спрашивает только про известные нам номера, поэтому ответ не зависит от
    того, сколько счетов у приложения всего, и пагинация не нужна. Возвращает
    None на любой ошибке: пустой список означал бы «провайдер не знает ни одного
    из этих счетов», а это другое утверждение, и на нём нельзя ничего решать.
    Токен не попадает ни в логи, ни в исключения.
    """
    if not invoice_ids:
        return []
    try:
        with httpx.Client() as client:
            response = client.get(
                f'{_CRYPTOBOT_BASE_URL}getInvoices',
                headers={'Crypto-Pay-API-Token': token},
                params={'invoice_ids': ','.join(str(number) for number in invoice_ids)},
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
    except Exception:
        logger.warning('cryptobot get_invoices failed count=%s', len(invoice_ids), exc_info=True)
        return None

    if not data.get('ok'):
        logger.warning('cryptobot get_invoices not ok error=%s', data.get('error'))
        return None

    result = data.get('result')
    # Провайдер отвечает объектом с ``items``; форма-список встречается в более
    # старых ответах того же метода и стоит одной строки совместимости.
    items = result.get('items') if isinstance(result, dict) else result
    if not isinstance(items, list):
        logger.warning('cryptobot get_invoices unexpected result shape')
        return None
    return [item for item in items if isinstance(item, dict)]


def verify_webhook_signature(token: str, body: bytes, signature: str) -> bool:
    """Verify a CryptoBot webhook signature.

    Signature: HMAC-SHA256(key=SHA256(token_bytes), msg=body_bytes).hexdigest()
    """
    key = hashlib.sha256(token.encode()).digest()
    expected = hmac.new(key, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
