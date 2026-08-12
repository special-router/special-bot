"""Отчёт на фикстуре, у которой каждое ожидаемое число посчитано руками."""
import datetime
from decimal import Decimal

from django.test import TestCase

from apps.analytics.backfill import backfill_billing_gaps, backfill_money_events
from apps.analytics.choices import MoneyEventKindChoices
from apps.analytics.models import MoneyEvent
from apps.analytics.reporting import build_report, format_report
from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.payments.models import Transaction
from apps.servers.models import Server, TariffServer
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


UTC = datetime.timezone.utc
START = datetime.date(2026, 7, 1)
END = datetime.date(2026, 7, 31)


class MoneyReportTests(TestCase):
    """Фикстура: трое пользователей, один из них приглашён первым.

    Деньги: u1 платит 420 (начислено 441, бонус 21), u2 платит 210 (без бонуса).
    Бесплатный баланс: промо 49 у u1 в июне и у u2 в июле, компенсация 30 у u3,
    начисление руками 500 у u3. Выплата рефереру u1 — 63. Выручка: три
    ежедневных списания у u1, два у u2, покупка подписки у u3, всего 42.
    """

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
        self.u1 = self.make_user(8001, datetime.date(2026, 6, 15))
        self.u2 = self.make_user(8002, datetime.date(2026, 7, 2), referral_user=self.u1)
        self.u3 = self.make_user(8003, datetime.date(2026, 7, 10))
        self.vpn1 = UserVPN.objects.create(user=self.u1, server=self.server)
        self.vpn2 = UserVPN.objects.create(user=self.u2, server=self.server)

        self.row(self.u1, TransactionSourceChoices.PROMO, '49', datetime.date(2026, 6, 20))
        self.row(self.u2, TransactionSourceChoices.PROMO, '49', datetime.date(2026, 7, 3))
        self.row(self.u1, TransactionSourceChoices.YOUMONEY, '441', datetime.date(2026, 7, 5))
        self.row(self.u2, TransactionSourceChoices.YOUMONEY, '210', datetime.date(2026, 7, 6))
        self.row(
            self.u1,
            TransactionSourceChoices.REFERRAL,
            '63',
            datetime.date(2026, 7, 6),
            from_referral_user=self.u2,
        )
        self.row(self.u3, TransactionSourceChoices.MANUAL, '500', datetime.date(2026, 7, 8))
        self.row(self.u3, TransactionSourceChoices.BUY, '-7', datetime.date(2026, 7, 8))
        for day in (10, 11, 12):
            self.charge(self.u1, self.vpn1, datetime.date(2026, 7, day))
        for day in (10, 11):
            self.charge(self.u2, self.vpn2, datetime.date(2026, 7, day))
        self.row(self.u1, TransactionSourceChoices.MANUAL, '-50', datetime.date(2026, 7, 15))
        self.row(self.u3, TransactionSourceChoices.COMPENSATION, '30', datetime.date(2026, 7, 20))

        # События строятся тем же путём, что и на живых данных.
        MoneyEvent.objects.all().delete()
        result = backfill_money_events()
        backfill_billing_gaps(until=END, result=result)
        self.report = build_report(START, END)

    def make_user(self, telegram_id: int, created_on: datetime.date, **kwargs) -> TelegramUser:
        user = TelegramUser.objects.create(telegram_id=telegram_id, username=f'u{telegram_id}', **kwargs)
        TelegramUser.objects.filter(pk=user.pk).update(created_at=self.moment(created_on))
        return TelegramUser.objects.get(pk=user.pk)

    def row(self, user, source: str, amount: str, created_on: datetime.date, **kwargs) -> Transaction:
        transaction = Transaction.objects.create(
            user=user,
            amount=Decimal(amount),
            status=TransactionStatusChoices.SUCCESS,
            source=source,
            **kwargs,
        )
        Transaction.objects.filter(pk=transaction.pk).update(created_at=self.moment(created_on))
        return Transaction.objects.get(pk=transaction.pk)

    def charge(self, user, user_vpn, day: datetime.date) -> Transaction:
        return self.row(
            user,
            TransactionSourceChoices.EVERYDAY_SYSTEM,
            '-7',
            day,
            user_vpn=user_vpn,
            charge_date=day,
        )

    @staticmethod
    def moment(day: datetime.date) -> datetime.datetime:
        return datetime.datetime.combine(day, datetime.time(0, 1), tzinfo=UTC)

    def test_cash_in_counts_only_provider_payments(self):
        cash = self.report['cash']
        # 420 у u1 плюс 210 у u2; бонус 21 деньгами не является.
        self.assertEqual(cash['total'], Decimal('630.00'))
        self.assertEqual(cash['provider_topup'], Decimal('630.00'))
        self.assertEqual(cash['provider_topup_rows'], 2)
        self.assertEqual(cash['rows_outside_bonus_ladder'], 0)

    def test_manual_credit_is_reported_apart_from_cash(self):
        cash = self.report['cash']
        self.assertEqual(cash['manual_credit_unknown_cash'], Decimal('500.00'))
        self.assertEqual(cash['manual_credit_rows'], 1)
        self.assertNotIn(Decimal('500.00'), (cash['total'], cash['provider_topup']))

    def test_recognised_revenue_is_the_charges_not_the_payments(self):
        revenue = self.report['revenue']
        self.assertEqual(revenue['daily_charge'], Decimal('35.00'))
        self.assertEqual(revenue['subscription_days'], 5)
        self.assertEqual(revenue['subscription_purchase'], Decimal('7.00'))
        self.assertEqual(revenue['total'], Decimal('42.00'))

    def test_revenue_funding_split_follows_each_account_inflow_mix(self):
        # u1: деньги 420 против бесплатных 49+21+63=133 → 420/553 от 21.00 = 15.949…
        # u2: деньги 210 против промо 49 → 210/259 от 14.00 = 11.351…
        # u3: денег не вносил → все 7.00 профинансированы выданным балансом.
        revenue = self.report['revenue']
        self.assertEqual(revenue['funded_by_cash'], Decimal('27.30'))
        self.assertEqual(revenue['funded_by_credit'], Decimal('14.70'))
        self.assertEqual(revenue['funded_by_cash_percent'], 65.0)

    def test_credit_granted_separates_what_each_promotion_costs(self):
        by_kind = self.report['credit_granted']['by_kind']
        self.assertEqual(by_kind[MoneyEventKindChoices.SIGNUP_PROMO]['amount'], Decimal('49.00'))
        self.assertEqual(by_kind[MoneyEventKindChoices.TOPUP]['amount'], Decimal('21.00'))
        self.assertEqual(by_kind[MoneyEventKindChoices.OUTAGE_COMPENSATION]['amount'], Decimal('30.00'))
        self.assertEqual(by_kind[MoneyEventKindChoices.MANUAL_CREDIT]['amount'], Decimal('500.00'))
        self.assertEqual(self.report['credit_granted']['total'], Decimal('600.00'))

    def test_payouts_and_adjustments_are_not_mixed_into_revenue(self):
        self.assertEqual(self.report['payouts']['referral'], Decimal('63.00'))
        self.assertEqual(self.report['payouts']['rows'], 1)
        self.assertEqual(self.report['adjustments']['balance_delta'], Decimal('-50.00'))
        self.assertEqual(self.report['adjustments']['rows'], 1)
        self.assertEqual(self.report['adjustments']['unclassified_rows'], 0)
        self.assertEqual(self.report['adjustments']['non_success_rows'], 0)

    def test_customer_counts_and_arpu(self):
        customers = self.report['customers']
        self.assertEqual(customers['active_users'], 3)
        self.assertEqual(customers['paying_users'], 2)
        self.assertEqual(customers['new_users'], 2)
        self.assertEqual(customers['first_time_payers'], 2)
        self.assertEqual(customers['arpu_revenue'], Decimal('14.00'))
        self.assertEqual(customers['arpu_cash'], Decimal('210.00'))

    def test_promo_conversion_looks_at_the_whole_history(self):
        promo = self.report['promo']
        self.assertEqual(promo['granted_in_period'], Decimal('49.00'))
        self.assertEqual(promo['recipients_lifetime'], 2)
        self.assertEqual(promo['converted_lifetime'], 2)
        self.assertEqual(promo['conversion_percent'], 100.0)
        self.assertEqual(promo['cost_lifetime'], Decimal('98.00'))
        self.assertEqual(promo['cash_from_converted_lifetime'], Decimal('630.00'))
        self.assertEqual(promo['margin_lifetime'], Decimal('532.00'))

    def test_referral_margin_compares_payouts_with_referred_cash(self):
        referral = self.report['referral']
        self.assertEqual(referral['referred_users'], 1)
        self.assertEqual(referral['payout_period'], Decimal('63.00'))
        self.assertEqual(referral['cash_from_referred_period'], Decimal('210.00'))
        self.assertEqual(referral['margin_period'], Decimal('147.00'))
        self.assertEqual(referral['margin_lifetime'], Decimal('147.00'))

    def test_churn_shows_both_accounts_falling_out_of_billing(self):
        churn = self.report['churn']
        # Списания u1 заканчиваются 12 июля, u2 — 11 июля; возвратов нет.
        self.assertEqual(churn['accounts_lapsed'], 2)
        self.assertEqual(churn['account_lapse_events'], 2)
        self.assertEqual(churn['accounts_resumed'], 0)
        self.assertEqual(churn['subscriptions_disabled_no_funds'], 0)

    def test_cohorts_are_lifetime_sums_by_signup_month(self):
        cohorts = {row['month']: row for row in self.report['cohorts']}
        self.assertEqual(cohorts['2026-06']['users'], 1)
        self.assertEqual(cohorts['2026-06']['cash'], Decimal('420.00'))
        self.assertEqual(cohorts['2026-06']['revenue'], Decimal('21.00'))
        self.assertEqual(cohorts['2026-06']['subscription_days_per_user'], 3.0)
        self.assertEqual(cohorts['2026-07']['users'], 2)
        self.assertEqual(cohorts['2026-07']['cash'], Decimal('210.00'))
        self.assertEqual(cohorts['2026-07']['cash_per_user'], Decimal('105.00'))
        self.assertEqual(cohorts['2026-07']['revenue_per_user'], Decimal('10.50'))
        self.assertEqual(cohorts['2026-07']['subscription_days_per_user'], 1.0)

    def test_text_output_carries_the_numbers_an_operator_reads(self):
        text = format_report(self.report)
        self.assertIn('money_report period=2026-07-01..2026-07-31', text)
        self.assertIn('received_total=630.00', text)
        self.assertIn('daily_charge=35.00 subscription_days=5', text)
        self.assertIn('funded_by_cash=27.30 (65.0%)', text)
        self.assertIn('manual_credit_unknown_cash=500.00 rows=1', text)
        self.assertIn('margin_period=147.00', text)


class EmptyPeriodTests(TestCase):
    def test_report_on_an_empty_period_prints_zeroes_rather_than_failing(self):
        report = build_report(START, END)

        self.assertEqual(report['cash']['total'], Decimal('0.00'))
        self.assertEqual(report['revenue']['funded_by_cash'], Decimal('0.00'))
        self.assertEqual(report['customers']['arpu_revenue'], Decimal('0.00'))
        self.assertEqual(report['promo']['conversion_percent'], 0.0)
        self.assertEqual(report['cohorts'], [])
        self.assertIn('received_total=0.00', format_report(report))
