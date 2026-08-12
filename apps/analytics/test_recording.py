import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from django.db.models import Sum
from django.test import TestCase, override_settings

from apps.analytics.choices import CashBasisChoices, EconomicClassChoices, FunnelStepChoices
from apps.analytics.models import FunnelEvent, MoneyEvent
from apps.analytics.recording import record_money_event, record_topup
from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.payments.models import Transaction
from apps.servers.models import Server, TariffServer
from apps.subscriptions.tasks import update_user_vpn
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


class MoneyEventRecordingTests(TestCase):
    def setUp(self):
        self.user = TelegramUser.objects.create(telegram_id=5001, username='payer')

    def make_transaction(self, source: str, amount: str, **kwargs) -> Transaction:
        return Transaction.objects.create(
            user=self.user,
            amount=Decimal(amount),
            status=TransactionStatusChoices.SUCCESS,
            source=source,
            **kwargs,
        )

    def test_committed_transaction_produces_one_event(self):
        with self.captureOnCommitCallbacks(execute=True):
            transaction = self.make_transaction(TransactionSourceChoices.YOUMONEY, '441')

        event = MoneyEvent.objects.get()
        self.assertEqual(event.event_key, f'tx:{transaction.pk}')
        self.assertEqual(event.economic_class, EconomicClassChoices.CASH_IN)
        self.assertEqual(event.cash_amount, Decimal('420.00'))
        self.assertEqual(event.credit_amount, Decimal('21.00'))
        self.assertEqual(event.balance_delta, Decimal('441.00'))

    def test_idempotency_key_stops_a_repeat_from_double_counting(self):
        with self.captureOnCommitCallbacks(execute=True):
            transaction = self.make_transaction(TransactionSourceChoices.YOUMONEY, '441')

        for _ in range(3):
            record_money_event(transaction)

        self.assertEqual(MoneyEvent.objects.count(), 1)
        self.assertEqual(MoneyEvent.objects.aggregate(total=Sum('cash_amount'))['total'], Decimal('420.00'))

    def test_measured_cash_upgrades_a_derived_event_without_adding_a_row(self):
        with self.captureOnCommitCallbacks(execute=True):
            transaction = self.make_transaction(TransactionSourceChoices.YOUMONEY, '3321')
        derived = MoneyEvent.objects.get()
        self.assertEqual(derived.cash_basis, CashBasisChoices.DERIVED)

        record_topup(transaction, cash_amount=Decimal('2555'))

        self.assertEqual(MoneyEvent.objects.count(), 1)
        refined = MoneyEvent.objects.get()
        self.assertEqual(refined.cash_basis, CashBasisChoices.MEASURED)
        self.assertEqual(refined.cash_amount, Decimal('2555.00'))
        self.assertEqual(refined.credit_amount, Decimal('766.00'))

    def test_daily_charge_event_is_dated_by_charge_date_not_creation(self):
        charge_date = datetime.date(2026, 7, 4)
        with self.captureOnCommitCallbacks(execute=True):
            self.make_transaction(TransactionSourceChoices.EVERYDAY_SYSTEM, '-7', charge_date=charge_date)

        event = MoneyEvent.objects.get()
        self.assertEqual(event.effective_date, charge_date)
        self.assertEqual(event.date_basis, 'CHARGE_DATE')

    def test_referral_payout_keeps_the_user_that_generated_it(self):
        referred = TelegramUser.objects.create(telegram_id=5002, username='referred')
        with self.captureOnCommitCallbacks(execute=True):
            self.make_transaction(
                TransactionSourceChoices.REFERRAL, '63', from_referral_user=referred
            )

        event = MoneyEvent.objects.get()
        self.assertEqual(event.referred_user_id, referred.id)
        self.assertEqual(event.payout_amount, Decimal('63.00'))

    @override_settings(ANALYTICS_EVENTS_ENABLED=False)
    def test_kill_switch_stops_the_extra_insert(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.make_transaction(TransactionSourceChoices.YOUMONEY, '441')

        self.assertEqual(MoneyEvent.objects.count(), 0)
        self.assertEqual(Transaction.objects.count(), 1)


class MoneyPathIsolationTests(TestCase):
    """Отказ аналитики не должен касаться денег."""

    def setUp(self):
        self.tariff = TariffServer.objects.create(name='base', price=Decimal('7.00'))
        self.server = Server.objects.create(
            name='test',
            ip_address='127.0.0.1',
            ssh_username='unused',
            ssh_password='unused',
            vpn_username='unused',
            vpn_password='unused',
            vpn_key='unused',
            vpn_url='https://panel.invalid',
            tariff=self.tariff,
        )
        self.user = TelegramUser.objects.create(telegram_id=6001, username='charged')
        Transaction.objects.create(
            user=self.user,
            amount=Decimal('100.00'),
            status=TransactionStatusChoices.SUCCESS,
            source=TransactionSourceChoices.MANUAL,
        )
        self.user_vpn = UserVPN.objects.create(user=self.user, server=self.server)

    def balance(self) -> Decimal:
        return TelegramUser.objects.filter(id=self.user.id).annotate_balance().values_list('balance', flat=True)[0]

    def test_failing_analytics_write_leaves_the_daily_charge_intact(self):
        with (
            patch('apps.analytics.recording.classify', side_effect=RuntimeError('analytics is down')),
            patch('apps.subscriptions.tasks.Bot') as bot_class,
            patch('apps.subscriptions.tasks.disable_vpn_user_from_server', new_callable=AsyncMock),
            patch('apps.subscriptions.tasks.time.sleep'),
            self.captureOnCommitCallbacks(execute=True),
        ):
            bot_class.return_value.send_message = AsyncMock()
            update_user_vpn()

        charges = Transaction.objects.filter_by_source(TransactionSourceChoices.EVERYDAY_SYSTEM)
        self.assertEqual(charges.count(), 1)
        self.assertEqual(charges.get().amount, Decimal('-7.00'))
        self.assertEqual(self.balance(), Decimal('93.00'))
        self.assertEqual(MoneyEvent.objects.count(), 0)

    def test_failing_funnel_write_leaves_the_disable_decision_intact(self):
        Transaction.objects.create(
            user=self.user,
            amount=Decimal('-100.00'),
            status=TransactionStatusChoices.SUCCESS,
            source=TransactionSourceChoices.MANUAL,
        )
        with (
            patch('apps.analytics.recording.FunnelEvent.objects.get_or_create', side_effect=RuntimeError('down')),
            patch('apps.subscriptions.tasks.Bot') as bot_class,
            patch('apps.subscriptions.tasks.disable_vpn_user_from_server', new_callable=AsyncMock) as disable,
            patch('apps.subscriptions.tasks.time.sleep'),
            self.captureOnCommitCallbacks(execute=True),
        ):
            bot_class.return_value.send_message = AsyncMock()
            update_user_vpn()

        disable.assert_awaited_once()
        self.assertEqual(FunnelEvent.objects.count(), 0)

    def test_billing_records_the_disable_as_a_churn_event(self):
        Transaction.objects.create(
            user=self.user,
            amount=Decimal('-100.00'),
            status=TransactionStatusChoices.SUCCESS,
            source=TransactionSourceChoices.MANUAL,
        )
        with (
            patch('apps.subscriptions.tasks.Bot') as bot_class,
            patch('apps.subscriptions.tasks.disable_vpn_user_from_server', new_callable=AsyncMock),
            patch('apps.subscriptions.tasks.time.sleep'),
            self.captureOnCommitCallbacks(execute=True),
        ):
            bot_class.return_value.send_message = AsyncMock()
            update_user_vpn()
            update_user_vpn()

        events = FunnelEvent.objects.filter(step=FunnelStepChoices.SUBSCRIPTION_DISABLED_NO_FUNDS)
        self.assertEqual(events.count(), 1)
        self.assertEqual(events.get().user_vpn_id, self.user_vpn.id)
