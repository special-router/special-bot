import hashlib
import hmac
import logging

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


def verify_webhook_signature(token: str, body: bytes, signature: str) -> bool:
    """Verify a CryptoBot webhook signature.

    Signature: HMAC-SHA256(key=SHA256(token_bytes), msg=body_bytes).hexdigest()
    """
    key = hashlib.sha256(token.encode()).digest()
    expected = hmac.new(key, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
