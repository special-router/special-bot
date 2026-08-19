"""Опрос CryptoBot: зачисление, идемпотентность и общая лестница бонусов."""
import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.payments.bonus import topup_bonus_amount
from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.payments.cryptobot_client import get_invoices_sync
from apps.payments.cryptobot_credit import credit_cryptobot_invoice
from apps.payments.models import CryptoBotInvoice, Transaction
from apps.payments.tasks import poll_cryptobot_invoices
from apps.users.models import TelegramUser


class TopupBonusLadderTests(TestCase):
    def test_below_first_threshold_credits_exactly_what_was_paid(self):
        self.assertEqual(topup_bonus_amount(Decimal('400')), Decimal('400'))
        self.assertEqual(topup_bonus_amount(Decimal('210')), Decimal('210'))

    def test_each_step_adds_its_percent(self):
        self.assertEqual(topup_bonus_amount(Decimal('500')), Decimal('525'))
        self.assertEqual(topup_bonus_amount(Decimal('700')), Decimal('770'))
        self.assertEqual(topup_bonus_amount(Decimal('2000')), Decimal('2400'))
        self.assertEqual(topup_bonus_amount(Decimal('3000')), Decimal('3900'))

    def test_thresholds_are_exclusive(self):
        # Ровно на пороге ступень ещё не сработала: 400 остаётся без бонуса, а
        # 600 получает надбавку предыдущей ступени, а не своей.
        self.assertEqual(topup_bonus_amount(Decimal('400')), Decimal('400'))
        self.assertEqual(topup_bonus_amount(Decimal('600')), Decimal('630'))
        self.assertEqual(topup_bonus_amount(Decimal('601')), Decimal('661'))

    def test_accepts_float_like_the_card_path_did(self):
        self.assertEqual(topup_bonus_amount(1300.0), Decimal('1560'))


class CreditCryptobotInvoiceTests(TestCase):
    def setUp(self):
        self.user = TelegramUser.objects.create(telegram_id=2001, username='crypto_payer')
        self.invoice = CryptoBotInvoice.objects.create(
            invoice_id=777,
            user=self.user,
            amount_rub=Decimal('700.00'),
            amount_usdt=Decimal('7.780000'),
        )

    def test_credits_with_volume_bonus_and_marks_paid(self):
        self.assertTrue(credit_cryptobot_invoice(self.invoice))

        transactions = list(Transaction.objects.filter(user=self.user))
        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0].source, TransactionSourceChoices.CRYPTO)
        self.assertEqual(transactions[0].status, TransactionStatusChoices.SUCCESS)
        # 700 ₽ попадает на ступень +10%, как и при оплате картой.
        self.assertEqual(transactions[0].amount, Decimal('770'))

        self.invoice.refresh_from_db()
        self.assertTrue(self.invoice.paid)

    def test_second_call_credits_nothing(self):
        self.assertTrue(credit_cryptobot_invoice(self.invoice))
        self.assertFalse(credit_cryptobot_invoice(self.invoice))
        self.assertEqual(Transaction.objects.filter(user=self.user).count(), 1)

    @override_settings(REFERRAL_PERCENT=30)
    def test_referrer_is_paid_its_percent(self):
        referrer = TelegramUser.objects.create(telegram_id=2002, username='referrer')
        self.user.referral_user = referrer
        self.user.save()
        invoice = CryptoBotInvoice.objects.select_related('user').get(id=self.invoice.id)

        self.assertTrue(credit_cryptobot_invoice(invoice))

        referral = Transaction.objects.get(user=referrer)
        self.assertEqual(referral.source, TransactionSourceChoices.REFERRAL)
        # 30% от зачисленных 770.
        self.assertEqual(referral.amount, Decimal('231'))


@override_settings(CRYPTOBOT_TOKEN='test:token', TELEGRAM_BOT_TOKEN='')
class PollCryptobotInvoicesTests(TestCase):
    def setUp(self):
        self.user = TelegramUser.objects.create(telegram_id=3001, username='poller')
        self.invoice = CryptoBotInvoice.objects.create(
            invoice_id=555,
            user=self.user,
            amount_rub=Decimal('300.00'),
            amount_usdt=Decimal('3.330000'),
        )

    def _patch_provider(self, items):
        return patch('apps.payments.tasks.get_invoices_sync', return_value=items)

    def test_paid_invoice_is_credited(self):
        with self._patch_provider([{'invoice_id': 555, 'status': 'paid'}]):
            credited = poll_cryptobot_invoices()

        self.assertEqual(credited, 1)
        self.assertEqual(Transaction.objects.filter(user=self.user).count(), 1)
        self.invoice.refresh_from_db()
        self.assertTrue(self.invoice.paid)

    def test_active_invoice_is_left_alone(self):
        with self._patch_provider([{'invoice_id': 555, 'status': 'active'}]):
            credited = poll_cryptobot_invoices()

        self.assertEqual(credited, 0)
        self.assertEqual(Transaction.objects.filter(user=self.user).count(), 0)
        self.invoice.refresh_from_db()
        self.assertFalse(self.invoice.paid)

    def test_repeated_polls_credit_once(self):
        with self._patch_provider([{'invoice_id': 555, 'status': 'paid'}]):
            poll_cryptobot_invoices()
            second = poll_cryptobot_invoices()

        self.assertEqual(second, 0)
        self.assertEqual(Transaction.objects.filter(user=self.user).count(), 1)

    def test_provider_failure_credits_nothing(self):
        with self._patch_provider(None):
            credited = poll_cryptobot_invoices()

        self.assertEqual(credited, 0)
        self.invoice.refresh_from_db()
        self.assertFalse(self.invoice.paid)

    def test_invoice_outside_the_window_is_not_polled(self):
        CryptoBotInvoice.objects.filter(id=self.invoice.id).update(
            created_at=timezone.now() - datetime.timedelta(hours=48))

        with patch('apps.payments.tasks.get_invoices_sync') as provider:
            credited = poll_cryptobot_invoices()

        provider.assert_not_called()
        self.assertEqual(credited, 0)

    def test_unknown_invoice_id_in_response_is_ignored(self):
        with self._patch_provider([{'invoice_id': 999, 'status': 'paid'}]):
            credited = poll_cryptobot_invoices()

        self.assertEqual(credited, 0)
        self.assertEqual(Transaction.objects.count(), 0)

    @override_settings(CRYPTOBOT_TOKEN='')
    def test_without_token_the_task_does_nothing(self):
        with patch('apps.payments.tasks.get_invoices_sync') as provider:
            credited = poll_cryptobot_invoices()

        provider.assert_not_called()
        self.assertEqual(credited, 0)


class GetInvoicesSyncTests(TestCase):
    def _make_client(self, response):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=response)
        return mock_client

    def _response(self, payload):
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json = MagicMock(return_value=payload)
        return response

    def test_empty_id_list_makes_no_request(self):
        with patch('apps.payments.cryptobot_client.httpx.Client') as client:
            self.assertEqual(get_invoices_sync('test:token', []), [])
        client.assert_not_called()

    def test_reads_items_from_the_result_object(self):
        payload = {'ok': True, 'result': {'items': [{'invoice_id': 1, 'status': 'paid'}]}}
        with patch('apps.payments.cryptobot_client.httpx.Client',
                   return_value=self._make_client(self._response(payload))):
            result = get_invoices_sync('test:token', [1])

        self.assertEqual(result, [{'invoice_id': 1, 'status': 'paid'}])

    def test_reads_a_bare_list_result(self):
        payload = {'ok': True, 'result': [{'invoice_id': 1, 'status': 'active'}]}
        with patch('apps.payments.cryptobot_client.httpx.Client',
                   return_value=self._make_client(self._response(payload))):
            result = get_invoices_sync('test:token', [1])

        self.assertEqual(result, [{'invoice_id': 1, 'status': 'active'}])

    def test_returns_none_when_ok_is_false(self):
        payload = {'ok': False, 'error': {'code': 'RATE_LIMIT'}}
        with patch('apps.payments.cryptobot_client.httpx.Client',
                   return_value=self._make_client(self._response(payload))):
            with self.assertLogs('apps.payments.cryptobot_client', level='WARNING'):
                result = get_invoices_sync('test:token', [1])

        self.assertIsNone(result)

    def test_returns_none_on_http_error(self):
        response = MagicMock()
        response.raise_for_status.side_effect = Exception('connection error')
        with patch('apps.payments.cryptobot_client.httpx.Client',
                   return_value=self._make_client(response)):
            with self.assertLogs('apps.payments.cryptobot_client', level='WARNING'):
                result = get_invoices_sync('test:token', [1])

        self.assertIsNone(result)
