"""Разложение баланса на реальные деньги и бонусы: инвариант важнее чисел.

Главная проверка одна и повторяется на всей матрице аккаунтов: сумма двух счетов
равна тому, что показывает ``annotate_balance()``. Если она разойдётся, спишется
не то, что видит пользователь, — поэтому расхождение обязано валить тест, а не
всплывать в отчёте через месяц.
"""
import datetime
from decimal import Decimal

from asgiref.sync import sync_to_async
from django.test import TestCase

from apps.analytics.balance_split import BalanceSplit, aggregate_split, attach_balance_split, split_balance
from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.payments.models import Transaction
from apps.telegram_bot.handlers.profile import build_profile_screen
from apps.users.models import TelegramUser


UTC = datetime.timezone.utc
ZERO = Decimal('0.00')


class BalanceSplitFixture(TestCase):
    """Матрица аккаунтов, покрывающая каждый способ попасть на баланс.

    Числа считаны руками: пустой аккаунт, только деньги, только промо, начисление
    руками, лестничный бонус, ушедший в минус, ушедший в минус и получивший
    подарок после, реферальная выплата, ручное списание, строки не в статусе
    SUCCESS и источник, которого таксономия не знает.
    """

    def setUp(self):
        self.empty = self.make_user(9000)

        self.cash_only = self.make_user(9001)
        self.row(self.cash_only, TransactionSourceChoices.YOUMONEY, '300', 1)
        self.charge(self.cash_only, 2)

        self.promo_only = self.make_user(9002)
        self.row(self.promo_only, TransactionSourceChoices.PROMO, '49', 1)
        self.charge(self.promo_only, 2)

        self.manual = self.make_user(9003)
        self.row(self.manual, TransactionSourceChoices.MANUAL, '500', 1)

        # 441 начислено = 420 платежом + 21 бонусом лестницы (+5% выше 400 ₽).
        self.ladder = self.make_user(9004)
        self.row(self.ladder, TransactionSourceChoices.YOUMONEY, '441', 1)

        self.overdrawn = self.make_user(9005)
        self.row(self.overdrawn, TransactionSourceChoices.YOUMONEY, '10', 1)
        self.charge(self.overdrawn, 2, amount='-14')

        self.overdrawn_then_gifted = self.make_user(9006)
        self.charge(self.overdrawn_then_gifted, 1, amount='-30')
        self.row(self.overdrawn_then_gifted, TransactionSourceChoices.COMPENSATION, '20', 2)

        self.referred = self.make_user(9007)
        self.row(self.referred, TransactionSourceChoices.REFERRAL, '63', 1)
        self.row(self.referred, TransactionSourceChoices.YOUMONEY, '100', 2)
        self.charge(self.referred, 3)

        self.corrected = self.make_user(9008)
        self.row(self.corrected, TransactionSourceChoices.MANUAL, '100', 1)
        self.row(self.corrected, TransactionSourceChoices.YOUMONEY, '50', 2)
        self.row(self.corrected, TransactionSourceChoices.MANUAL, '-30', 3)

        # PENDING и FAILED влияют на баланс: ``annotate_balance`` не фильтрует
        # статус, и разложение обязано считать ровно то же множество строк.
        self.pending = self.make_user(9009)
        self.row(self.pending, TransactionSourceChoices.YOUMONEY, '200', 1, status=TransactionStatusChoices.PENDING)
        self.row(self.pending, TransactionSourceChoices.PROMO, '49', 2, status=TransactionStatusChoices.FAILED)

        self.unknown_source = self.make_user(9010)
        Transaction.objects.filter(
            pk=self.row(self.unknown_source, TransactionSourceChoices.MANUAL, '77', 1).pk
        ).update(source='SBP_QR')

        self.users = TelegramUser.objects.filter(telegram_id__gte=9000)

    def make_user(self, telegram_id: int) -> TelegramUser:
        return TelegramUser.objects.create(telegram_id=telegram_id, username=f'u{telegram_id}')

    def row(self, user, source: str, amount: str, day: int, **kwargs) -> Transaction:
        kwargs.setdefault('status', TransactionStatusChoices.SUCCESS)
        transaction = Transaction.objects.create(user=user, amount=Decimal(amount), source=source, **kwargs)
        Transaction.objects.filter(pk=transaction.pk).update(created_at=self.moment(day))
        return transaction

    def charge(self, user, day: int, amount: str = '-7') -> Transaction:
        return self.row(
            user,
            TransactionSourceChoices.EVERYDAY_SYSTEM,
            amount,
            day,
            charge_date=datetime.date(2026, 7, day),
        )

    @staticmethod
    def moment(day: int) -> datetime.datetime:
        return datetime.datetime(2026, 7, day, 0, 1, tzinfo=UTC)

    def ledger_balance(self, user) -> Decimal:
        return TelegramUser.objects.filter(id=user.id).annotate_balance().values_list('balance', flat=True)[0]


class BalanceSplitInvariantTests(BalanceSplitFixture):
    def test_pots_always_sum_to_the_ledger_balance(self):
        """Единственная проверка, которую нельзя ослабить ни для одного аккаунта."""
        for user in self.users:
            with self.subTest(telegram_id=user.telegram_id):
                split = split_balance(user.id)
                self.assertEqual(split.total, Decimal(self.ledger_balance(user)))

    def test_bonus_is_never_negative(self):
        for user in self.users:
            with self.subTest(telegram_id=user.telegram_id):
                self.assertGreaterEqual(split_balance(user.id).bonus, ZERO)

    def test_an_account_with_no_grants_is_exactly_todays_behaviour(self):
        """Тот, кому ничего не дарили, обязан вести себя как до разделения."""
        split = split_balance(self.cash_only.id)

        self.assertEqual(split.bonus, ZERO)
        self.assertEqual(split.real, Decimal('293.00'))
        self.assertEqual(split.total, Decimal(self.ledger_balance(self.cash_only)))

    def test_an_account_with_no_transactions_splits_into_zeroes(self):
        split = split_balance(self.empty.id)

        self.assertEqual((split.real, split.bonus, split.total), (ZERO, ZERO, ZERO))

    def test_non_success_rows_count_exactly_as_they_do_in_the_balance(self):
        split = split_balance(self.pending.id)

        self.assertEqual(split.total, Decimal(self.ledger_balance(self.pending)))
        self.assertEqual(split.real, Decimal('200.00'))
        self.assertEqual(split.bonus, Decimal('49.00'))


class BonusFirstConsumptionTests(BalanceSplitFixture):
    def test_a_charge_takes_the_bonus_before_the_money(self):
        split = split_balance(self.referred.id)

        # 63 выплаты — подарок, 100 — деньги; списание 7 берётся из подарка.
        self.assertEqual(split.bonus, Decimal('56.00'))
        self.assertEqual(split.real, Decimal('100.00'))

    def test_promo_is_spent_and_leaves_nothing_behind(self):
        split = split_balance(self.promo_only.id)

        self.assertEqual(split.bonus, Decimal('42.00'))
        self.assertEqual(split.real, ZERO)

    def test_manual_credit_is_a_gift_until_history_says_otherwise(self):
        split = split_balance(self.manual.id)

        self.assertEqual(split.bonus, Decimal('500.00'))
        self.assertEqual(split.real, ZERO)

    def test_a_ladder_topup_is_split_the_way_the_report_splits_it(self):
        split = split_balance(self.ladder.id)

        self.assertEqual(split.real, Decimal('420.00'))
        self.assertEqual(split.bonus, Decimal('21.00'))

    def test_a_manual_debit_also_takes_the_bonus_first(self):
        split = split_balance(self.corrected.id)

        # 100 подарено, 50 деньгами, списание руками 30 уходит из подарка.
        self.assertEqual(split.bonus, Decimal('70.00'))
        self.assertEqual(split.real, Decimal('50.00'))

    def test_an_unknown_source_is_not_quietly_called_a_gift(self):
        split = split_balance(self.unknown_source.id)

        self.assertEqual(split.bonus, ZERO)
        self.assertEqual(split.real, Decimal('77.00'))
        self.assertEqual(split.unclassified, Decimal('77.00'))


class OverdrawnAccountTests(BalanceSplitFixture):
    def test_the_whole_overdraft_sits_on_the_real_pot(self):
        split = split_balance(self.overdrawn.id)

        self.assertEqual(split.real, Decimal('-4.00'))
        self.assertEqual(split.bonus, ZERO)
        self.assertEqual(split.total, Decimal(self.ledger_balance(self.overdrawn)))

    def test_a_gift_to_an_overdrawn_account_pays_the_debt_before_it_shows_up(self):
        """Иначе экран обещал бы бонус аккаунту, чей общий баланс отрицателен."""
        split = split_balance(self.overdrawn_then_gifted.id)

        self.assertEqual(split.total, Decimal('-10.00'))
        self.assertEqual(split.bonus, ZERO)
        self.assertEqual(split.real, Decimal('-10.00'))


class PointInTimeTests(BalanceSplitFixture):
    def test_a_past_moment_ignores_everything_recorded_later(self):
        before_charge = split_balance(self.promo_only.id, as_of=self.moment(1))

        self.assertEqual(before_charge.bonus, Decimal('49.00'))
        self.assertEqual(before_charge.total, Decimal('49.00'))

    def test_a_date_means_the_end_of_that_day(self):
        split = split_balance(self.promo_only.id, as_of=datetime.date(2026, 7, 1))

        self.assertEqual(split.total, Decimal('49.00'))

    def test_a_moment_before_any_row_gives_an_empty_split(self):
        split = split_balance(self.manual.id, as_of=datetime.date(2026, 6, 30))

        self.assertEqual(split.total, ZERO)


class ProfileScreenAgainstRealLedgerTests(BalanceSplitFixture):
    """Единственный тест экрана без подмен: от транзакций до текста профиля.

    Всё остальное про интерфейс живёт в ``test_ui.py`` на заданном разложении.
    Здесь проверяется связка целиком — в том числе то, что защитный ``except``
    в ``balance_state_lines`` не глотает работающий путь.
    """

    async def test_the_screen_shows_the_ledger_total_and_the_real_bonus(self):
        # Итог печатается ровно тем, что вернул ``annotate_balance``: на SQLite
        # это ``120``, на PostgreSQL — ``120.00``. Формат итога не трогается.
        expected = await sync_to_async(self.ledger_balance)(self.corrected)
        text, _keyboard = await build_profile_screen(self.corrected)

        self.assertIn(f'Баланс: {expected} руб.', text)
        self.assertIn('В том числе бонусных: 70.00 руб.', text)

    async def test_an_account_without_grants_gets_the_line_it_had_before(self):
        expected = await sync_to_async(self.ledger_balance)(self.cash_only)
        text, _keyboard = await build_profile_screen(self.cash_only)

        self.assertIn(f'Баланс: {expected} руб.', text)
        self.assertNotIn('бонусных', text)


class BulkSplitTests(BalanceSplitFixture):
    def test_attaching_the_split_matches_computing_it_one_by_one(self):
        attached = attach_balance_split(self.users)

        for user in attached:
            with self.subTest(telegram_id=user.telegram_id):
                self.assertEqual(user.balance_split, split_balance(user.id))

    def test_a_user_without_transactions_still_gets_a_split(self):
        attached = {user.id: user.balance_split for user in attach_balance_split(self.users)}

        self.assertEqual(attached[self.empty.id], BalanceSplit())

    def test_the_aggregate_reconciles_against_the_ledger(self):
        totals = aggregate_split()

        self.assertEqual(totals['mismatched_accounts'], 0)
        self.assertEqual(
            totals['ledger_total'],
            sum((Decimal(self.ledger_balance(user)) for user in self.users), ZERO),
        )
        self.assertEqual(totals['real_total'] + totals['bonus_total'], totals['ledger_total'])
        self.assertEqual(totals['accounts_overdrawn'], 2)
        self.assertEqual(totals['unclassified_total'], Decimal('77.00'))
