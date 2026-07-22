import hashlib
import hmac
import json
from typing import Any

import httpx
from django.conf import settings


class CryptoBotClient:
    def __init__(self, token: str | None = None, testnet: bool | None = None):
        self._token = token or settings.CRYPTOBOT_TOKEN
        self._testnet = testnet if testnet is not None else settings.CRYPTOBOT_TESTNET
        self._base_url = (
            'https://testnet-pay.crypt.bot/api/'
            if self._testnet
            else 'https://pay.crypt.bot/api/'
        )

    def _headers(self) -> dict[str, str]:
        return {
            'Crypto-Pay-API-Token': self._token,
            'Content-Type': 'application/json',
        }

    async def create_invoice(
        self,
        amount_rub: float,
        description: str,
        payload: str,
        currency_type: str = 'fiat',
        fiat: str = 'RUB',
        asset: str = '',
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            'currency_type': currency_type,
            'amount': str(amount_rub),
            'description': description[:1024],
            'payload': payload[:4096],
            'expires_in': 3600,
        }
        if currency_type == 'fiat':
            body['fiat'] = fiat
        elif asset:
            body['asset'] = asset
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f'{self._base_url}createInvoice',
                headers=self._headers(),
                json=body,
            )
            response.raise_for_status()
            data = response.json()
            if not data.get('ok'):
                raise ValueError(data.get('error', 'CryptoBot API error'))
            return data['result']

    @staticmethod
    def verify_signature(body: bytes, signature: str, token: str) -> bool:
        secret = hashlib.sha256(token.encode()).digest()
        check_signature = hmac.new(secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(check_signature, signature)

    @staticmethod
    def parse_payload(payload: str) -> dict[str, Any]:
        return json.loads(payload)
