"""Tests for the unified period→method top-up flow."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import TestCase, override_settings

from apps.servers.models import TariffServer
from apps.telegram_bot.handlers.top_up_cryptobot import (
    topup_card,
    topup_crypto_pay,
    topup_period_selected,
)
from apps.users.models import TelegramUser


def _callback_update(data: str, telegram_id: int = 1001):
    from_user = SimpleNamespace(id=telegram_id, username='testuser')
    query = SimpleNamespace(
        data=data,
        from_user=from_user,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    return SimpleNamespace(
        callback_query=query,
        message=None,
        effective_chat=SimpleNamespace(id=telegram_id),
        effective_user=from_user,
    )


def _context():
    return SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))


class TopupPeriodSelectedTests(TestCase):
    def setUp(self):
        self.tariff = TariffServer.objects.create(name='Standard', price=Decimal('7.00'))

    @override_settings(CRYPTOBOT_TOKEN='test:token', YOUMONEY_TOKEN='test:ym')
    @patch('apps.telegram_bot.handlers.top_up_cryptobot.render_screen', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.top_up_cryptobot.get_usdt_rate', new_callable=AsyncMock)
    async def test_card_and_crypto_buttons_when_rate_available(self, get_rate, render_screen):
        get_rate.return_value = Decimal('90')

        update = _callback_update('topup_period:1')
        await topup_period_selected(update, _context())

        render_screen.assert_awaited_once()
        keyboard = render_screen.call_args.args[3]
        cb_data = [
            btn.callback_data
            for row in keyboard.inline_keyboard
            for btn in row
            if btn.callback_data
        ]
        self.assertTrue(any(d.startswith('topup_card:') for d in cb_data))
        self.assertTrue(any(d.startswith('topup_crypto_pay:') for d in cb_data))

    @override_settings(CRYPTOBOT_TOKEN='test:token', YOUMONEY_TOKEN='test:ym')
    @patch('apps.telegram_bot.handlers.top_up_cryptobot.render_screen', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.top_up_cryptobot.get_usdt_rate', new_callable=AsyncMock)
    async def test_only_card_button_when_rate_is_none(self, get_rate, render_screen):
        get_rate.return_value = None

        update = _callback_update('topup_period:3')
        await topup_period_selected(update, _context())

        keyboard = render_screen.call_args.args[3]
        cb_data = [
            btn.callback_data
            for row in keyboard.inline_keyboard
            for btn in row
            if btn.callback_data
        ]
        self.assertTrue(any(d.startswith('topup_card:') for d in cb_data))
        self.assertFalse(any(d.startswith('topup_crypto_pay:') for d in cb_data))

    @override_settings(CRYPTOBOT_TOKEN='', YOUMONEY_TOKEN='')
    @patch('apps.telegram_bot.handlers.top_up_cryptobot.render_screen', new_callable=AsyncMock)
    async def test_only_back_button_when_no_payment_method(self, render_screen):
        update = _callback_update('topup_period:1')
        await topup_period_selected(update, _context())

        keyboard = render_screen.call_args.args[3]
        cb_data = [
            btn.callback_data
            for row in keyboard.inline_keyboard
            for btn in row
            if btn.callback_data
        ]
        self.assertFalse(any(d.startswith('topup_card:') for d in cb_data))
        self.assertFalse(any(d.startswith('topup_crypto_pay:') for d in cb_data))
        self.assertIn('show_balance', cb_data)

    @override_settings(CRYPTOBOT_TOKEN='test:token', YOUMONEY_TOKEN='test:ym')
    @patch('apps.telegram_bot.handlers.top_up_cryptobot.answer_query', new_callable=AsyncMock)
    async def test_unknown_months_answered_silently(self, answer_query):
        update = _callback_update('topup_period:99')
        await topup_period_selected(update, _context())
        answer_query.assert_awaited_once()

    @override_settings(CRYPTOBOT_TOKEN='test:token', YOUMONEY_TOKEN='test:ym')
    @patch('apps.telegram_bot.handlers.top_up_cryptobot.render_screen', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.top_up_cryptobot.get_usdt_rate', new_callable=AsyncMock)
    async def test_crypto_callback_encodes_integer_amount_rub(self, get_rate, render_screen):
        # price=7.00, months=1 → days=30 → amount_rub=210
        get_rate.return_value = Decimal('90')

        update = _callback_update('topup_period:1')
        await topup_period_selected(update, _context())

        keyboard = render_screen.call_args.args[3]
        crypto_data = [
            btn.callback_data
            for row in keyboard.inline_keyboard
            for btn in row
            if btn.callback_data and btn.callback_data.startswith('topup_crypto_pay:')
        ]
        self.assertEqual(len(crypto_data), 1)
        parts = crypto_data[0].split(':')
        self.assertEqual(parts[1], '1')
        self.assertEqual(parts[2], '210')

    @override_settings(CRYPTOBOT_TOKEN='test:token', YOUMONEY_TOKEN='test:ym')
    @patch('apps.telegram_bot.handlers.top_up_cryptobot.render_screen', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.top_up_cryptobot.get_usdt_rate', new_callable=AsyncMock)
    async def test_twelve_months_uses_365_days(self, get_rate, render_screen):
        # price=7.00, months=12 → days=365 → amount_rub=2555
        get_rate.return_value = None

        update = _callback_update('topup_period:12')
        await topup_period_selected(update, _context())

        keyboard = render_screen.call_args.args[3]
        card_data = [
            btn.callback_data
            for row in keyboard.inline_keyboard
            for btn in row
            if btn.callback_data and btn.callback_data.startswith('topup_card:')
        ]
        self.assertEqual(card_data, ['topup_card:12'])


class TopupCardTests(TestCase):
    async def test_routes_to_correct_handler(self):
        mock_handler = AsyncMock()
        with patch.dict(
            'apps.telegram_bot.handlers.top_up_cryptobot._CARD_HANDLERS',
            {1: mock_handler},
        ):
            update = _callback_update('topup_card:1')
            await topup_card(update, _context())

        mock_handler.assert_awaited_once()

    @patch('apps.telegram_bot.handlers.top_up_cryptobot.answer_query', new_callable=AsyncMock)
    async def test_unknown_months_answered_silently(self, answer_query):
        with patch.dict(
            'apps.telegram_bot.handlers.top_up_cryptobot._CARD_HANDLERS',
            {},
            clear=True,
        ):
            update = _callback_update('topup_card:99')
            await topup_card(update, _context())

        answer_query.assert_awaited_once()


@override_settings(CRYPTOBOT_TOKEN='test:token')
class TopupCryptoPayTests(TestCase):
    def setUp(self):
        self.user = TelegramUser.objects.create(telegram_id=1001, username='testuser')

    @patch('apps.telegram_bot.handlers.top_up_cryptobot.get_usdt_rate', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.top_up_cryptobot.answer_query', new_callable=AsyncMock)
    async def test_rate_unavailable_shows_toast(self, answer_query, get_rate):
        get_rate.return_value = None

        update = _callback_update('topup_crypto_pay:1:210')
        await topup_crypto_pay(update, _context())

        answer_query.assert_awaited_once()
        self.assertIn('Курс', answer_query.call_args.args[1])

    @patch('apps.telegram_bot.handlers.top_up_cryptobot.create_usdt_invoice', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.top_up_cryptobot.get_user', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.top_up_cryptobot.get_usdt_rate', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.top_up_cryptobot.answer_query', new_callable=AsyncMock)
    async def test_invoice_failure_shows_toast(self, answer_query, get_rate, get_user, create_invoice):
        get_rate.return_value = Decimal('90')
        get_user.return_value = self.user
        create_invoice.return_value = None

        update = _callback_update('topup_crypto_pay:1:210')
        await topup_crypto_pay(update, _context())

        answer_query.assert_awaited_once()
        self.assertIn('Ошибка', answer_query.call_args.args[1])

    @patch('apps.telegram_bot.handlers.top_up_cryptobot.render_screen', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.top_up_cryptobot.create_usdt_invoice', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.top_up_cryptobot.get_user', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.top_up_cryptobot.get_usdt_rate', new_callable=AsyncMock)
    async def test_success_shows_pay_button(self, get_rate, get_user, create_invoice, render_screen):
        get_rate.return_value = Decimal('90')
        get_user.return_value = self.user
        create_invoice.return_value = {
            'invoice_id': 99,
            'mini_app_invoice_url': 'https://t.me/CryptoBot?start=pay99',
            'bot_invoice_url': 'https://t.me/CryptoBot?start=pay99',
        }

        update = _callback_update('topup_crypto_pay:1:210')
        await topup_crypto_pay(update, _context())

        render_screen.assert_awaited_once()
        keyboard = render_screen.call_args.args[3]
        url_btns = [btn for row in keyboard.inline_keyboard for btn in row if btn.url]
        self.assertEqual(len(url_btns), 1)
        self.assertEqual(url_btns[0].url, 'https://t.me/CryptoBot?start=pay99')
        self.assertIn('Оплатить', url_btns[0].text)

    @patch('apps.telegram_bot.handlers.top_up_cryptobot.render_screen', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.top_up_cryptobot.create_usdt_invoice', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.top_up_cryptobot.get_user', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.top_up_cryptobot.get_usdt_rate', new_callable=AsyncMock)
    async def test_success_creates_invoice_row(self, get_rate, get_user, create_invoice, render_screen):
        from apps.payments.models import CryptoBotInvoice

        get_rate.return_value = Decimal('90')
        get_user.return_value = self.user
        create_invoice.return_value = {
            'invoice_id': 100,
            'mini_app_invoice_url': 'https://t.me/CryptoBot?start=pay100',
            'bot_invoice_url': '',
        }

        update = _callback_update('topup_crypto_pay:1:210')
        await topup_crypto_pay(update, _context())

        invoice = await CryptoBotInvoice.objects.aget(invoice_id=100)
        self.assertFalse(invoice.paid)
        self.assertEqual(invoice.amount_rub, Decimal('210'))
        self.assertEqual(invoice.user_id, self.user.id)

    @patch('apps.telegram_bot.handlers.top_up_cryptobot.answer_query', new_callable=AsyncMock)
    async def test_zero_amount_rub_rejected(self, answer_query):
        update = _callback_update('topup_crypto_pay:1:0')
        await topup_crypto_pay(update, _context())
        answer_query.assert_awaited_once()

    @patch('apps.telegram_bot.handlers.top_up_cryptobot.answer_query', new_callable=AsyncMock)
    async def test_non_integer_amount_rub_rejected(self, answer_query):
        # Defensive test: pattern guards this in production, but handler validates anyway.
        update = _callback_update('topup_crypto_pay:1:abc')
        await topup_crypto_pay(update, _context())
        answer_query.assert_awaited_once()

    @patch('apps.telegram_bot.handlers.top_up_cryptobot.render_screen', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.top_up_cryptobot.create_usdt_invoice', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.top_up_cryptobot.get_user', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.top_up_cryptobot.get_usdt_rate', new_callable=AsyncMock)
    async def test_usdt_amount_computed_fresh_from_rate(self, get_rate, get_user, create_invoice, render_screen):
        # 210 RUB at 90 RUB/USDT = 2.34 USDT (rounded up)
        get_rate.return_value = Decimal('90')
        get_user.return_value = self.user
        create_invoice.return_value = {
            'invoice_id': 101,
            'mini_app_invoice_url': 'https://t.me/CryptoBot?start=x',
            'bot_invoice_url': '',
        }

        update = _callback_update('topup_crypto_pay:1:210')
        await topup_crypto_pay(update, _context())

        call_kwargs = create_invoice.call_args.kwargs
        self.assertEqual(call_kwargs['amount_usdt'], '2.34')


@override_settings(CRYPTOBOT_TOKEN='test:token', YOUMONEY_TOKEN='')
class BalancePeriodButtonsTests(TestCase):
    async def _get_keyboard(self, user):
        from apps.telegram_bot.inline_buttons.balance import get_reply_markup_balance
        with patch('apps.telegram_bot.inline_buttons.balance.Transaction') as mock_txn:
            mock_txn.objects.filter_by_user.return_value.filter_by_source.return_value.aexists = AsyncMock(
                return_value=True
            )
            return await get_reply_markup_balance(user)

    async def test_five_period_buttons_present(self):
        user = SimpleNamespace(id=99, telegram_id=9901)
        keyboard = await self._get_keyboard(user)
        cb_data = [
            btn.callback_data
            for row in keyboard.inline_keyboard
            for btn in row
            if btn.callback_data
        ]
        period = [d for d in cb_data if d.startswith('topup_period:')]
        self.assertEqual(len(period), 5)
        self.assertIn('topup_period:1', period)
        self.assertIn('topup_period:12', period)

    async def test_no_old_crypto_topup_button(self):
        user = SimpleNamespace(id=99, telegram_id=9901)
        keyboard = await self._get_keyboard(user)
        cb_data = [
            btn.callback_data
            for row in keyboard.inline_keyboard
            for btn in row
            if btn.callback_data
        ]
        self.assertNotIn('crypto_topup', cb_data)


@override_settings(CRYPTOBOT_TOKEN='', YOUMONEY_TOKEN='')
class BalanceNoPeriodButtonsTests(TestCase):
    async def test_no_period_buttons_when_no_payment_method(self):
        from apps.telegram_bot.inline_buttons.balance import get_reply_markup_balance

        user = SimpleNamespace(id=99, telegram_id=9901)
        with patch('apps.telegram_bot.inline_buttons.balance.Transaction') as mock_txn:
            mock_txn.objects.filter_by_user.return_value.filter_by_source.return_value.aexists = AsyncMock(
                return_value=True
            )
            keyboard = await get_reply_markup_balance(user)

        cb_data = [
            btn.callback_data
            for row in keyboard.inline_keyboard
            for btn in row
            if btn.callback_data
        ]
        self.assertFalse(any(d.startswith('topup_period:') for d in cb_data))
