"""Воронка на живых обработчиках: нажатия идут через них, а не через API шагов.

Тест ходит по тем же функциям, которые регистрирует бот, и заканчивается
``money_report``: проверяется не то, что ``record_funnel_event`` умеет писать
строку, а то, что раздел FUNNEL в отчёте владельца перестал быть нулевым.
Вызов шагов напрямую этого поймать не мог — обработчик до шага мог не доходить.
"""
import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from asgiref.sync import async_to_sync
from django.test import TestCase, override_settings

from apps.analytics.choices import FunnelStepChoices
from apps.analytics.models import FunnelEvent, MoneyEvent
from apps.analytics.reporting import build_report, format_report
from apps.payments.choices import TransactionSourceChoices
from apps.payments.models import Transaction
from apps.servers.models import TariffServer
from apps.telegram_bot.handlers.balance import show_balance
from apps.telegram_bot.handlers.top_up_balance import (
    pre_checkout_callback,
    successful_payment_callback,
    top_up_balance_one_month,
    top_up_balance_promo,
)
from apps.users.models import TelegramUser


# Токен по форме, но не по содержанию: `payments_enabled` смотрит только на то,
# пуст ли он, а `send_invoice` здесь подделан и никуда не ходит.
PROVIDER_TOKEN = '390540012:TEST:not-a-real-token'
CLIENT_ID = 7001
CHARGE_ID = 'synthetic-charge-id'
PAID_KOPECKS = 21000


def _context():
    return SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock(), send_invoice=AsyncMock()))


def _callback_update():
    """Апдейт-подделка: обработчикам нужны только эти поля."""
    from_user = SimpleNamespace(id=CLIENT_ID, username='client', is_bot=False)
    query = SimpleNamespace(from_user=from_user, answer=AsyncMock(), edit_message_text=AsyncMock())
    return SimpleNamespace(
        callback_query=query,
        message=None,
        effective_chat=SimpleNamespace(id=CLIENT_ID),
        effective_user=from_user,
    )


def _pre_checkout_update():
    """У настоящего pre-checkout нет ни callback_query, ни message."""
    return SimpleNamespace(
        pre_checkout_query=SimpleNamespace(
            from_user=SimpleNamespace(id=CLIENT_ID, username='client', is_bot=False),
            total_amount=PAID_KOPECKS,
            answer=AsyncMock(),
        ),
        callback_query=None,
        message=None,
    )


def _payment_update():
    from_user = SimpleNamespace(id=CLIENT_ID, username='client', is_bot=False)
    return SimpleNamespace(
        callback_query=None,
        message=SimpleNamespace(
            from_user=from_user,
            successful_payment=SimpleNamespace(
                total_amount=PAID_KOPECKS,
                telegram_payment_charge_id=CHARGE_ID,
            ),
        ),
        effective_chat=SimpleNamespace(id=CLIENT_ID),
        effective_user=from_user,
    )


def _funnel_of_today() -> dict:
    """Раздел FUNNEL за окно вокруг сегодня: события датируются по UTC."""
    today = datetime.date.today()
    report = build_report(today - datetime.timedelta(days=1), today + datetime.timedelta(days=1))
    return report['funnel']


@override_settings(YOUMONEY_TOKEN=PROVIDER_TOKEN)
class FunnelWalkTests(TestCase):
    """Один пользователь проходит путь целиком; отчёт считает его шаги."""

    def setUp(self):
        TariffServer.objects.create(name='base', price=Decimal('7.00'))

    @staticmethod
    async def _press_every_button():
        context = _context()
        await show_balance(_callback_update(), context)
        await top_up_balance_promo(_callback_update(), context)
        await top_up_balance_one_month(_callback_update(), context)
        await pre_checkout_callback(_pre_checkout_update(), context)
        await successful_payment_callback(_payment_update(), context)

    def walk(self):
        # Обработчики асинхронные, а `captureOnCommitCallbacks` — синхронный
        # менеджер: событие денег ставится на `on_commit`, и без него в TestCase
        # оно бы не записалось никогда.
        async_to_sync(self._press_every_button)()

    def test_a_full_walk_leaves_every_funnel_step_non_zero(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.walk()

        self.assertEqual(
            _funnel_of_today(),
            {
                FunnelStepChoices.BALANCE_SCREEN_SHOWN: 1,
                FunnelStepChoices.PROMO_CLAIMED: 1,
                FunnelStepChoices.TOPUP_PLAN_CHOSEN: 1,
                FunnelStepChoices.INVOICE_SENT: 1,
                FunnelStepChoices.PRE_CHECKOUT_APPROVED: 1,
                FunnelStepChoices.PAYMENT_COMPLETED: 1,
            },
        )

    def test_the_printed_report_shows_counts_where_it_used_to_show_zeros(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.walk()

        printed = format_report(build_report(
            datetime.date.today() - datetime.timedelta(days=1),
            datetime.date.today() + datetime.timedelta(days=1),
        ))

        self.assertIn('balance_screen_shown=1', printed)
        self.assertIn('invoice_sent=1', printed)
        self.assertIn('payment_completed=1', printed)
        self.assertNotIn('=0\n', printed.split('FUNNEL\n', 1)[1])

    def test_a_repeated_update_does_not_double_a_step(self):
        """Telegram повторяет апдейт при таймауте, а экран нажимают многократно."""
        with self.captureOnCommitCallbacks(execute=True):
            self.walk()
            self.walk()

        self.assertEqual(
            FunnelEvent.objects.filter(step=FunnelStepChoices.BALANCE_SCREEN_SHOWN).count(), 1)
        self.assertEqual(
            FunnelEvent.objects.filter(step=FunnelStepChoices.PAYMENT_COMPLETED).count(), 1)

    def test_the_measured_payment_refines_the_amount_derived_from_the_ladder(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.walk()

        topup = MoneyEvent.objects.get(source=TransactionSourceChoices.YOUMONEY)
        # 210 рублей: объёмный бонус на этой сумме не срабатывает, поэтому
        # начисленное и измеренное совпадают — и воронка обязана назвать то же.
        self.assertEqual(topup.cash_amount, Decimal('210.00'))
        self.assertEqual(
            FunnelEvent.objects.get(step=FunnelStepChoices.PAYMENT_COMPLETED).amount,
            Decimal('210.00'),
        )

    def test_a_failing_event_log_leaves_the_payment_alone(self):
        """Отказ журнала обязан остаться в логе и не тронуть строку пополнения."""
        broken = Mock()
        broken.objects.get_or_create.side_effect = RuntimeError('analytics is down')

        with patch('apps.analytics.recording.FunnelEvent', broken):
            with self.assertLogs('apps.analytics.recording', level='ERROR'):
                with self.captureOnCommitCallbacks(execute=True):
                    self.walk()

        self.assertEqual(FunnelEvent.objects.count(), 0)
        self.assertEqual(
            Transaction.objects.filter(source=TransactionSourceChoices.YOUMONEY).count(), 1)


@override_settings(YOUMONEY_TOKEN='')
class FunnelWithoutAProviderTests(TestCase):
    """Сегодняшняя продакшн-картина: кнопки сумм скрыты, провайдера нет."""

    def setUp(self):
        TariffServer.objects.create(name='base', price=Decimal('7.00'))

    @staticmethod
    async def _press_up_to_the_invoice():
        context = _context()
        await show_balance(_callback_update(), context)
        await top_up_balance_promo(_callback_update(), context)
        # Нажатие приходит с ранее разосланного экрана, где кнопки сумм ещё были.
        await top_up_balance_one_month(_callback_update(), context)

    def test_the_walk_stops_exactly_at_the_invoice(self):
        with self.captureOnCommitCallbacks(execute=True):
            async_to_sync(self._press_up_to_the_invoice)()

        funnel = _funnel_of_today()

        # Выбор срока записан, счёт — нет. Это и есть тот обрыв, ради которого
        # шаг стоит до проверки провайдера, а не рядом с отправкой счёта.
        self.assertEqual(funnel[FunnelStepChoices.BALANCE_SCREEN_SHOWN], 1)
        self.assertEqual(funnel[FunnelStepChoices.PROMO_CLAIMED], 1)
        self.assertEqual(funnel[FunnelStepChoices.TOPUP_PLAN_CHOSEN], 1)
        self.assertEqual(funnel[FunnelStepChoices.INVOICE_SENT], 0)
        self.assertEqual(funnel[FunnelStepChoices.PRE_CHECKOUT_APPROVED], 0)
        self.assertEqual(funnel[FunnelStepChoices.PAYMENT_COMPLETED], 0)

    def test_the_promo_is_still_granted_and_counted(self):
        with self.captureOnCommitCallbacks(execute=True):
            async_to_sync(self._press_up_to_the_invoice)()

        self.assertEqual(TelegramUser.objects.filter(telegram_id=CLIENT_ID).count(), 1)
        self.assertEqual(
            Transaction.objects.filter(source=TransactionSourceChoices.PROMO).count(), 1)
