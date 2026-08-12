import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.payments.models import Transaction
from apps.servers.models import Server, TariffServer
from apps.subscriptions.tasks import update_user_vpn
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


class DailyBillingTests(TestCase):
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
            client_vpn_host='127.0.0.1',
            tariff=self.tariff,
        )

    def make_user(self, telegram_id: int, balance: str = None) -> TelegramUser:
        user = TelegramUser.objects.create(telegram_id=telegram_id, username=f'user{telegram_id}')
        if balance is not None:
            Transaction.objects.create(
                user=user,
                amount=Decimal(balance),
                status=TransactionStatusChoices.SUCCESS,
                source=TransactionSourceChoices.MANUAL,
            )
        return user

    def add_subscription(self, user: TelegramUser, days_ago: int) -> UserVPN:
        user_vpn = UserVPN.objects.create(user=user, server=self.server)
        # created_at заполняется auto_now_add, поэтому возраст подписки задаем отдельно.
        UserVPN.objects.filter(id=user_vpn.id).update(created_at=timezone.now() - datetime.timedelta(days=days_ago))
        return UserVPN.objects.get(id=user_vpn.id)

    def balance_of(self, user: TelegramUser) -> Decimal:
        return TelegramUser.objects.filter(id=user.id).annotate_balance().values_list('balance', flat=True).first()

    def charges_for(self, user_vpn: UserVPN) -> int:
        return (
            Transaction.objects.filter_by_source(TransactionSourceChoices.EVERYDAY_SYSTEM)
            .filter(user_vpn=user_vpn)
            .count()
        )

    def run_billing(self):
        """Прогоняет задачу, подменяя панель и телеграм. Возвращает (disable, send_message)."""
        with (
            patch('apps.subscriptions.tasks.Bot') as bot_class,
            patch('apps.subscriptions.tasks.disable_vpn_user_from_server', new_callable=AsyncMock) as disable,
            patch('apps.subscriptions.tasks.time.sleep'),
        ):
            bot_class.return_value.send_message = AsyncMock()
            update_user_vpn()
            return disable, bot_class.return_value.send_message

    def test_partially_funded_account_keeps_oldest_and_disables_newest(self):
        user = self.make_user(1001, balance='15.00')
        oldest = self.add_subscription(user, days_ago=3)
        middle = self.add_subscription(user, days_ago=2)
        newest = self.add_subscription(user, days_ago=1)

        disable, _ = self.run_billing()

        self.assertEqual(self.charges_for(oldest), 1)
        self.assertEqual(self.charges_for(middle), 1)
        self.assertEqual(self.charges_for(newest), 0)
        self.assertEqual(disable.await_args_list, [((newest,), {})])
        self.assertEqual(self.balance_of(user), Decimal('1.00'))

    def test_fully_funded_account_charges_every_subscription(self):
        user = self.make_user(1002, balance='21.00')
        subscriptions = [self.add_subscription(user, days_ago=days) for days in (3, 2, 1)]

        disable, _ = self.run_billing()

        for user_vpn in subscriptions:
            self.assertEqual(self.charges_for(user_vpn), 1)
        disable.assert_not_awaited()
        self.assertEqual(self.balance_of(user), Decimal('0.00'))

    def test_zero_balance_is_disabled_without_a_negative_transaction(self):
        user = self.make_user(1003)
        user_vpn = self.add_subscription(user, days_ago=1)

        disable, send_message = self.run_billing()

        self.assertEqual(self.charges_for(user_vpn), 0)
        self.assertEqual(disable.await_args_list, [((user_vpn,), {})])
        self.assertEqual(self.balance_of(user), Decimal('0.00'))
        self.assertEqual(
            send_message.await_args_list,
            [((), {'chat_id': 1003, 'text': 'Закончились деньги на балансе. Доступ к услугам остановлен'})],
        )

    def test_second_run_on_the_same_day_charges_nothing(self):
        user = self.make_user(1004, balance='21.00')
        subscriptions = [self.add_subscription(user, days_ago=days) for days in (3, 2, 1)]

        self.run_billing()
        disable, send_message = self.run_billing()

        for user_vpn in subscriptions:
            self.assertEqual(self.charges_for(user_vpn), 1)
        disable.assert_not_awaited()
        send_message.assert_not_awaited()
        self.assertEqual(self.balance_of(user), Decimal('0.00'))

    def test_low_balance_warning_follows_the_running_balance(self):
        user = self.make_user(1005, balance='21.00')
        self.add_subscription(user, days_ago=2)
        self.add_subscription(user, days_ago=1)

        _, send_message = self.run_billing()

        # После первого списания остается 14.00 — денег хватает больше чем на день,
        # предупреждение уходит только после второго, когда остаток падает до 7.00.
        self.assertEqual(
            send_message.await_args_list,
            [((), {'chat_id': 1005, 'text': 'Пополните баланс, денег осталось на 1 день'})],
        )

    def test_account_already_in_debt_is_disabled_without_deepening_the_debt(self):
        # Состояние, оставленное прежней логикой: баланс уже отрицательный.
        user = self.make_user(1009, balance='-25.00')
        user_vpn = self.add_subscription(user, days_ago=1)

        disable, send_message = self.run_billing()

        self.assertEqual(self.charges_for(user_vpn), 0)
        self.assertEqual(self.balance_of(user), Decimal('-25.00'))
        self.assertEqual(disable.await_args_list, [((user_vpn,), {})])
        self.assertEqual(len(send_message.await_args_list), 1)

    def test_account_in_debt_with_already_disabled_subscription_is_left_alone(self):
        user = self.make_user(1010, balance='-25.00')
        user_vpn = self.add_subscription(user, days_ago=1)
        UserVPN.objects.filter(id=user_vpn.id).update(enabled=False)

        disable, send_message = self.run_billing()

        self.assertEqual(self.charges_for(user_vpn), 0)
        self.assertEqual(self.balance_of(user), Decimal('-25.00'))
        disable.assert_not_awaited()
        send_message.assert_not_awaited()

    def test_disabled_subscriptions_are_not_charged(self):
        user = self.make_user(1006, balance='21.00')
        user_vpn = self.add_subscription(user, days_ago=1)
        UserVPN.objects.filter(id=user_vpn.id).update(enabled=False)

        self.run_billing()

        self.assertEqual(self.charges_for(user_vpn), 0)
        self.assertEqual(self.balance_of(user), Decimal('21.00'))

    def test_accounts_are_billed_independently_of_each_other(self):
        rich = self.make_user(1007, balance='21.00')
        rich_vpn = self.add_subscription(rich, days_ago=1)
        poor = self.make_user(1008, balance='3.00')
        poor_vpn = self.add_subscription(poor, days_ago=1)

        disable, _ = self.run_billing()

        self.assertEqual(self.charges_for(rich_vpn), 1)
        self.assertEqual(self.charges_for(poor_vpn), 0)
        self.assertEqual(disable.await_args_list, [((poor_vpn,), {})])
        self.assertEqual(self.balance_of(poor), Decimal('3.00'))


class DailyChargeConstraintTests(TestCase):
    def setUp(self):
        tariff = TariffServer.objects.create(name='base', price=Decimal('7.00'))
        server = Server.objects.create(
            name='test',
            ip_address='127.0.0.1',
            ssh_username='unused',
            ssh_password='unused',
            vpn_username='unused',
            vpn_password='unused',
            vpn_key='unused',
            vpn_url='https://panel.invalid',
            client_vpn_host='127.0.0.1',
            tariff=tariff,
        )
        self.user = TelegramUser.objects.create(telegram_id=2001, username='user2001')
        self.user_vpn = UserVPN.objects.create(user=self.user, server=server)

    def make_charge(self, **kwargs):
        return Transaction.objects.create(
            user=self.user,
            amount=Decimal('-7.00'),
            status=TransactionStatusChoices.SUCCESS,
            source=TransactionSourceChoices.EVERYDAY_SYSTEM,
            **kwargs,
        )

    def test_second_charge_for_the_same_subscription_and_day_is_rejected(self):
        charge_date = datetime.date(2026, 1, 1)
        self.make_charge(user_vpn=self.user_vpn, charge_date=charge_date)

        with self.assertRaises(IntegrityError):
            self.make_charge(user_vpn=self.user_vpn, charge_date=charge_date)

    def test_charges_on_different_days_are_allowed(self):
        self.make_charge(user_vpn=self.user_vpn, charge_date=datetime.date(2026, 1, 1))
        self.make_charge(user_vpn=self.user_vpn, charge_date=datetime.date(2026, 1, 2))

        self.assertEqual(Transaction.objects.filter_by_user(self.user.id).count(), 2)

    def test_legacy_charges_without_a_subscription_do_not_collide(self):
        self.make_charge()
        self.make_charge()

        self.assertEqual(Transaction.objects.filter_by_user(self.user.id).count(), 2)
