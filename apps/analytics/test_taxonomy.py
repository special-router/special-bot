from decimal import Decimal

from django.test import SimpleTestCase

from apps.analytics.choices import CashBasisChoices, EconomicClassChoices, MoneyEventKindChoices
from apps.analytics.taxonomy import classify, known_sources, split_topup
from apps.payments.choices import TransactionSourceChoices


def _apply_ladder(cash: Decimal) -> Decimal:
    """Точная копия начисления из ``successful_payment_callback``."""
    amount = float(cash)
    if amount > 2520:
        return Decimal(int(amount + amount * 0.3))
    if amount > 1250:
        return Decimal(int(amount + amount * 0.2))
    if amount > 600:
        return Decimal(int(amount + amount * 0.1))
    if amount > 400:
        return Decimal(int(amount + amount * 0.05))
    return cash


class TaxonomyTests(SimpleTestCase):
    def test_every_current_source_has_an_economic_meaning(self):
        expected = {
            TransactionSourceChoices.YOUMONEY: (
                EconomicClassChoices.CASH_IN,
                MoneyEventKindChoices.TOPUP,
                Decimal('441'),
            ),
            TransactionSourceChoices.PROMO: (
                EconomicClassChoices.CREDIT_GRANTED,
                MoneyEventKindChoices.SIGNUP_PROMO,
                Decimal('49'),
            ),
            TransactionSourceChoices.COMPENSATION: (
                EconomicClassChoices.CREDIT_GRANTED,
                MoneyEventKindChoices.OUTAGE_COMPENSATION,
                Decimal('30'),
            ),
            TransactionSourceChoices.MANUAL: (
                EconomicClassChoices.CREDIT_GRANTED,
                MoneyEventKindChoices.MANUAL_CREDIT,
                Decimal('500'),
            ),
            TransactionSourceChoices.REFERRAL: (
                EconomicClassChoices.PAYOUT,
                MoneyEventKindChoices.REFERRAL_PAYOUT,
                Decimal('63'),
            ),
            TransactionSourceChoices.EVERYDAY_SYSTEM: (
                EconomicClassChoices.REVENUE,
                MoneyEventKindChoices.DAILY_CHARGE,
                Decimal('-7'),
            ),
            TransactionSourceChoices.BUY: (
                EconomicClassChoices.REVENUE,
                MoneyEventKindChoices.SUBSCRIPTION_PURCHASE,
                Decimal('-7'),
            ),
        }
        self.assertEqual(set(expected), set(TransactionSourceChoices.values))
        self.assertEqual(set(expected), set(known_sources()))
        for source, (economic_class, kind, amount) in expected.items():
            with self.subTest(source=source):
                result = classify(source, amount)
                self.assertEqual(result.economic_class, economic_class)
                self.assertEqual(result.kind, kind)
                self.assertEqual(result.balance_delta, amount)

    def test_slice_of_each_source_lands_in_exactly_one_bucket(self):
        cases = {
            TransactionSourceChoices.YOUMONEY: (Decimal('441'), 'cash_amount', Decimal('420.00')),
            TransactionSourceChoices.PROMO: (Decimal('49'), 'credit_amount', Decimal('49')),
            TransactionSourceChoices.COMPENSATION: (Decimal('30'), 'credit_amount', Decimal('30')),
            TransactionSourceChoices.MANUAL: (Decimal('500'), 'credit_amount', Decimal('500')),
            TransactionSourceChoices.REFERRAL: (Decimal('63'), 'payout_amount', Decimal('63')),
            TransactionSourceChoices.EVERYDAY_SYSTEM: (Decimal('-7'), 'revenue_amount', Decimal('7')),
            TransactionSourceChoices.BUY: (Decimal('-7'), 'revenue_amount', Decimal('7')),
        }
        for source, (amount, field, expected) in cases.items():
            with self.subTest(source=source):
                result = classify(source, amount)
                self.assertEqual(getattr(result, field), expected)

    def test_manual_credit_never_claims_to_be_cash(self):
        result = classify(TransactionSourceChoices.MANUAL, Decimal('191386'))
        self.assertEqual(result.cash_basis, CashBasisChoices.UNKNOWN)
        self.assertEqual(result.cash_amount, Decimal('0.00'))
        self.assertEqual(result.credit_amount, Decimal('191386'))

    def test_negative_manual_is_an_adjustment_not_a_grant(self):
        result = classify(TransactionSourceChoices.MANUAL, Decimal('-50'))
        self.assertEqual(result.economic_class, EconomicClassChoices.ADJUSTMENT)
        self.assertEqual(result.kind, MoneyEventKindChoices.MANUAL_ADJUSTMENT)
        self.assertEqual(result.credit_amount, Decimal('0.00'))

    def test_unexpected_sign_is_a_reversal_not_revenue(self):
        for source in (TransactionSourceChoices.EVERYDAY_SYSTEM, TransactionSourceChoices.BUY):
            with self.subTest(source=source):
                result = classify(source, Decimal('7'))
                self.assertEqual(result.economic_class, EconomicClassChoices.ADJUSTMENT)
                self.assertEqual(result.kind, MoneyEventKindChoices.REVERSAL)
                self.assertEqual(result.revenue_amount, Decimal('0.00'))
        for source in (TransactionSourceChoices.PROMO, TransactionSourceChoices.REFERRAL):
            with self.subTest(source=source):
                result = classify(source, Decimal('-49'))
                self.assertEqual(result.kind, MoneyEventKindChoices.REVERSAL)

    def test_zero_amount_is_a_no_op(self):
        result = classify(TransactionSourceChoices.MANUAL, Decimal('0'))
        self.assertEqual(result.kind, MoneyEventKindChoices.NO_OP)

    def test_future_source_is_flagged_not_silently_counted(self):
        result = classify('CRYPTO', Decimal('1000'))
        self.assertEqual(result.economic_class, EconomicClassChoices.UNKNOWN)
        self.assertEqual(result.kind, MoneyEventKindChoices.UNCLASSIFIED)
        self.assertEqual(result.cash_amount, Decimal('0.00'))
        self.assertEqual(result.credit_amount, Decimal('0.00'))
        self.assertEqual(result.balance_delta, Decimal('1000'))


class TopupSplitTests(SimpleTestCase):
    def test_ladder_inverts_to_within_a_rouble_on_every_offered_plan(self):
        # Суммы кнопок пополнения при цене 7 ₽/день: 30, 60, 90, 180 и 365 дней.
        for cash in ('210', '420', '630', '1260', '2555'):
            with self.subTest(cash=cash):
                credited = _apply_ladder(Decimal(cash))
                recovered, bonus, basis = split_topup(credited)
                self.assertEqual(basis, CashBasisChoices.DERIVED)
                self.assertLess(abs(recovered - Decimal(cash)), Decimal('1'))
                self.assertEqual(recovered + bonus, credited)

    def test_tier_boundaries_stay_on_the_expected_side(self):
        for cash in ('400', '400.01', '600', '600.01', '1250', '1250.01', '2520', '2520.01'):
            with self.subTest(cash=cash):
                recovered, _, basis = split_topup(_apply_ladder(Decimal(cash)))
                self.assertEqual(basis, CashBasisChoices.DERIVED)
                self.assertLess(abs(recovered - Decimal(cash)), Decimal('1'))

    def test_amount_no_ladder_step_can_produce_is_marked_unknown(self):
        # 3100 лежит в разрыве между ступенями 1.2 и 1.3: такой суммы лестница
        # выдать не могла, поэтому доля бонуса неизвестна.
        cash, bonus, basis = split_topup(Decimal('3100'))
        self.assertEqual(basis, CashBasisChoices.UNKNOWN)
        self.assertEqual(cash, Decimal('3100'))
        self.assertEqual(bonus, Decimal('0.00'))

    def test_measured_cash_beats_the_derived_split(self):
        result = classify(TransactionSourceChoices.YOUMONEY, Decimal('3321'), measured_cash=Decimal('2555'))
        self.assertEqual(result.cash_basis, CashBasisChoices.MEASURED)
        self.assertEqual(result.cash_amount, Decimal('2555'))
        self.assertEqual(result.credit_amount, Decimal('766'))
