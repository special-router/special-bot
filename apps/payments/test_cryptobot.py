"""Tests for CryptoBot webhook verification, signature, and invoice client."""
import hashlib
import hmac
import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import TestCase, override_settings

from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.payments.cryptobot_client import create_usdt_invoice, verify_webhook_signature
from apps.payments.models import CryptoBotInvoice, Transaction
from apps.users.models import TelegramUser


def _make_signature(token: str, body: bytes) -> str:
    key = hashlib.sha256(token.encode()).digest()
    return hmac.new(key, body, hashlib.sha256).hexdigest()


class VerifyWebhookSignatureTests(TestCase):
    def test_correct_signature_returns_true(self):
        token = 'test:token123'
        body = b'{"update_type":"invoice_paid"}'
        sig = _make_signature(token, body)
        self.assertTrue(verify_webhook_signature(token, body, sig))

    def test_wrong_signature_returns_false(self):
        token = 'test:token123'
        body = b'{"update_type":"invoice_paid"}'
        self.assertFalse(verify_webhook_signature(token, body, 'deadbeef' * 8))

    def test_wrong_token_returns_false(self):
        body = b'{"update_type":"invoice_paid"}'
        sig = _make_signature('correct:token', body)
        self.assertFalse(verify_webhook_signature('wrong:token', body, sig))


@override_settings(CRYPTOBOT_TOKEN='test:token')
class CryptobotWebhookViewTests(TestCase):
    def setUp(self):
        self.user = TelegramUser.objects.create(telegram_id=1001, username='testuser')
        self.invoice = CryptoBotInvoice.objects.create(
            invoice_id=42,
            user=self.user,
            amount_rub=Decimal('300.00'),
            amount_usdt=Decimal('3.330000'),
        )

    def _post(self, payload: dict, token: str = 'test:token') -> object:
        body = json.dumps(payload).encode()
        sig = _make_signature(token, body)
        return self.client.post(
            '/api/webhook/cryptobot/',
            data=body,
            content_type='application/json',
            HTTP_CRYPTO_PAY_API_SIGNATURE=sig,
        )

    def test_bad_signature_returns_401(self):
        body = b'{"update_type":"invoice_paid"}'
        response = self.client.post(
            '/api/webhook/cryptobot/',
            data=body,
            content_type='application/json',
            HTTP_CRYPTO_PAY_API_SIGNATURE='badsig',
        )
        self.assertEqual(response.status_code, 401)

    def test_idempotent_on_already_paid_invoice(self):
        self.invoice.paid = True
        self.invoice.save()

        payload = {
            'update_type': 'invoice_paid',
            'payload': {'invoice_id': 42, 'amount': '3.33', 'asset': 'USDT'},
        }
        response = self._post(payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Transaction.objects.filter(user=self.user).count(), 0)

    def test_idempotent_on_unknown_invoice_id(self):
        payload = {
            'update_type': 'invoice_paid',
            'payload': {'invoice_id': 99999, 'amount': '3.33', 'asset': 'USDT'},
        }
        response = self._post(payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Transaction.objects.filter(user=self.user).count(), 0)

    def test_valid_payload_creates_transaction_and_marks_paid(self):
        payload = {
            'update_type': 'invoice_paid',
            'payload': {'invoice_id': 42, 'amount': '3.33', 'asset': 'USDT'},
        }
        response = self._post(payload)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['ok'])

        transactions = list(Transaction.objects.filter(user=self.user))
        self.assertEqual(len(transactions), 1)
        txn = transactions[0]
        self.assertEqual(txn.source, TransactionSourceChoices.CRYPTO)
        self.assertEqual(txn.status, TransactionStatusChoices.SUCCESS)
        self.assertEqual(txn.amount, Decimal('300.00'))

        self.invoice.refresh_from_db()
        self.assertTrue(self.invoice.paid)

    def test_non_invoice_paid_update_type_is_ignored(self):
        payload = {'update_type': 'something_else', 'payload': {}}
        response = self._post(payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Transaction.objects.filter(user=self.user).count(), 0)


class CreateUsdtInvoiceTests(TestCase):
    async def test_returns_none_and_logs_warning_on_http_error(self):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception('connection error')

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch('apps.payments.cryptobot_client.httpx.AsyncClient', return_value=mock_client):
            with self.assertLogs('apps.payments.cryptobot_client', level='WARNING') as log:
                result = await create_usdt_invoice(
                    token='test:token',
                    amount_usdt='3.33',
                    user_db_id=1,
                    description='Test',
                )

        self.assertIsNone(result)
        self.assertTrue(any('create_invoice failed' in line for line in log.output))

    async def test_returns_none_when_ok_is_false(self):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={'ok': False, 'error': {'code': 'RATE_LIMIT'}})

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch('apps.payments.cryptobot_client.httpx.AsyncClient', return_value=mock_client):
            with self.assertLogs('apps.payments.cryptobot_client', level='WARNING') as log:
                result = await create_usdt_invoice(
                    token='test:token',
                    amount_usdt='3.33',
                    user_db_id=1,
                    description='Test',
                )

        self.assertIsNone(result)
        self.assertTrue(any('not ok' in line for line in log.output))
