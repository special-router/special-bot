import datetime
from decimal import Decimal

from django.test import TestCase

from apps.analytics.backfill import backfill_billing_gaps, backfill_money_events
from apps.analytics.choices import CashBasisChoices, EventOriginChoices, FunnelStepChoices
from apps.analytics.models import FunnelEvent, MoneyEvent
from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.payments.models import Transaction
from apps.users.models import TelegramUser


UTC = datetime.timezone.utc


class BackfillTests(TestCase):
    def setUp(self):
        self.user = TelegramUser.objects.create(telegram_id=7001, username='historic')

    def make_transaction(self, source: str, amount: str, created_on: datetime.date, **kwargs) -> Transaction:
        """Строка с заданной датой: ``created_at`` — auto_now_add, поэтому его правим отдельно."""
        row = Transaction.objects.create(
            user=self.user,
            amount=Decimal(amount),
            status=TransactionStatusChoices.SUCCESS,
            source=source,
            **kwargs,
        )
        Transaction.objects.filter(pk=row.pk).update(
            created_at=datetime.datetime.combine(created_on, datetime.time(0, 1), tzinfo=UTC)
        )
        return Transaction.objects.get(pk=row.pk)

    def test_backfill_is_idempotent_across_repeated_runs(self):
        self.make_transaction(TransactionSourceChoices.YOUMONEY, '441', datetime.date(2026, 7, 5))
        self.make_transaction(TransactionSourceChoices.MANUAL, '500', datetime.date(2026, 7, 6))
        self.make_transaction(TransactionSourceChoices.EVERYDAY_SYSTEM, '-7', datetime.date(2026, 7, 7))

        first = backfill_money_events()
        second = backfill_money_events()

        self.assertEqual(first.money_events_created, 3)
        self.assertEqual(second.money_events_created, 0)
        self.assertEqual(second.transactions_seen, 3)
        self.assertEqual(MoneyEvent.objects.count(), 3)

    def test_backfill_fills_only_what_the_live_path_missed(self):
        with self.captureOnCommitCallbacks(execute=True):
            live = Transaction.objects.create(
                user=self.user,
                amount=Decimal('49'),
                status=TransactionStatusChoices.SUCCESS,
                source=TransactionSourceChoices.PROMO,
            )
        missed = self.make_transaction(TransactionSourceChoices.MANUAL, '500', datetime.date(2026, 7, 6))
        MoneyEvent.objects.filter(transaction=missed).delete()

        result = backfill_money_events()

        self.assertEqual(result.money_events_created, 1)
        self.assertEqual(MoneyEvent.objects.count(), 2)
        self.assertEqual(MoneyEvent.objects.get(transaction=live).origin, EventOriginChoices.LIVE)
        self.assertEqual(MoneyEvent.objects.get(transaction=missed).origin, EventOriginChoices.BACKFILL)

    def test_refresh_reclassifies_without_creating_a_second_event(self):
        row = self.make_transaction(TransactionSourceChoices.YOUMONEY, '441', datetime.date(2026, 7, 5))
        backfill_money_events()
        MoneyEvent.objects.filter(transaction=row).update(
            cash_amount=Decimal('0.00'), cash_basis=CashBasisChoices.UNKNOWN
        )

        result = backfill_money_events(refresh=True)

        self.assertEqual(result.money_events_created, 0)
        self.assertEqual(result.money_events_refreshed, 1)
        self.assertEqual(MoneyEvent.objects.count(), 1)
        self.assertEqual(MoneyEvent.objects.get().cash_amount, Decimal('420.00'))

    def test_unknown_source_is_reported_rather_than_dropped(self):
        self.make_transaction('CRYPTO', '1000', datetime.date(2026, 7, 5))

        result = backfill_money_events()

        self.assertEqual(result.unknown_sources, {'CRYPTO': 1})
        self.assertEqual(MoneyEvent.objects.get().economic_class, 'UNKNOWN')

    def test_window_limits_the_rows_examined(self):
        self.make_transaction(TransactionSourceChoices.MANUAL, '100', datetime.date(2026, 6, 30))
        self.make_transaction(TransactionSourceChoices.MANUAL, '200', datetime.date(2026, 7, 2))

        result = backfill_money_events(since=datetime.date(2026, 7, 1))

        self.assertEqual(result.transactions_seen, 1)
        self.assertEqual(MoneyEvent.objects.count(), 1)


class BillingGapTests(TestCase):
    def setUp(self):
        self.user = TelegramUser.objects.create(telegram_id=7002, username='lapsing')

    def charge_on(self, day: datetime.date, *, with_charge_date: bool = True) -> None:
        row = Transaction.objects.create(
            user=self.user,
            amount=Decimal('-7.00'),
            status=TransactionStatusChoices.SUCCESS,
            source=TransactionSourceChoices.EVERYDAY_SYSTEM,
            charge_date=day if with_charge_date else None,
        )
        Transaction.objects.filter(pk=row.pk).update(
            created_at=datetime.datetime.combine(day, datetime.time(0, 1), tzinfo=UTC)
        )

    def test_gap_in_the_charge_series_becomes_a_lapse_and_a_resume(self):
        for day in (1, 2, 3, 8, 9):
            self.charge_on(datetime.date(2026, 7, day))

        backfill_billing_gaps(until=datetime.date(2026, 7, 31))

        lapsed = FunnelEvent.objects.filter(step=FunnelStepChoices.ACCOUNT_BILLING_LAPSED)
        resumed = FunnelEvent.objects.filter(step=FunnelStepChoices.ACCOUNT_BILLING_RESUMED)
        # Пропуск после 3 июля и возврат 8-го, плюс отвал после 9-го до конца окна.
        self.assertEqual(
            sorted(lapsed.values_list('effective_date', flat=True)),
            [datetime.date(2026, 7, 4), datetime.date(2026, 7, 10)],
        )
        self.assertEqual(list(resumed.values_list('effective_date', flat=True)), [datetime.date(2026, 7, 8)])

    def test_gap_backfill_is_idempotent(self):
        for day in (1, 2, 8):
            self.charge_on(datetime.date(2026, 7, day))

        backfill_billing_gaps(until=datetime.date(2026, 7, 31))
        second = backfill_billing_gaps(until=datetime.date(2026, 7, 31))

        self.assertEqual(second.lapses_created, 0)
        self.assertEqual(second.resumes_created, 0)
        self.assertEqual(FunnelEvent.objects.count(), 3)

    def test_rows_without_charge_date_fall_back_to_creation_day(self):
        self.charge_on(datetime.date(2026, 7, 1), with_charge_date=False)
        self.charge_on(datetime.date(2026, 7, 5), with_charge_date=False)

        backfill_billing_gaps(until=datetime.date(2026, 7, 7))

        self.assertEqual(
            sorted(
                FunnelEvent.objects.filter(step=FunnelStepChoices.ACCOUNT_BILLING_LAPSED).values_list(
                    'effective_date', flat=True
                )
            ),
            [datetime.date(2026, 7, 2), datetime.date(2026, 7, 6)],
        )

    def test_uninterrupted_series_produces_nothing(self):
        for day in (1, 2, 3):
            self.charge_on(datetime.date(2026, 7, day))

        result = backfill_billing_gaps(until=datetime.date(2026, 7, 3))

        self.assertEqual(result.lapses_created, 0)
        self.assertEqual(FunnelEvent.objects.count(), 0)
