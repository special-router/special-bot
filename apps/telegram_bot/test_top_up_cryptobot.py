"""Tests for CryptoBot top-up handler and balance screen fork."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import TestCase, override_settings

from apps.telegram_bot.handlers.top_up_cryptobot import show_crypto_topup, crypto_amount_selected
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


class ShowCryptoTopupTests(TestCase):
    @patch('apps.telegram_bot.handlers.top_up_cryptobot.render_screen', new_callable=AsyncMock)
    async def test_shows_five_amount_buttons(self, render_screen):
        update = _callback_update('crypto_topup')
        await show_crypto_topup(update, _context())

        render_screen.assert_awaited_once()
        _text, keyboard = render_screen.call_args.args[2], render_screen.call_args.args[3]

        # Collect all callback_data values from inline buttons
        button_data = [
            btn.callback_data
            for row in keyboard.inline_keyboard
            for btn in row
            if btn.callback_data
        ]
        amount_buttons = [d for d in button_data if d.startswith('crypto_topup:')]
        self.assertEqual(len(amount_buttons), 5)


@override_settings(CRYPTOBOT_TOKEN='test:token', CRYPTOBOT_USDT_RATE='90')
class CryptoAmountSelectedTests(TestCase):
    def setUp(self):
        self.user = TelegramUser.objects.create(telegram_id=1001, username='testuser')

    @patch('apps.telegram_bot.handlers.top_up_cryptobot.create_usdt_invoice', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.top_up_cryptobot.get_user', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.top_up_cryptobot.answer_query', new_callable=AsyncMock)
    async def test_none_from_client_sends_toast(self, answer_query, get_user, create_invoice):
        get_user.return_value = self.user
        create_invoice.return_value = None

        update = _callback_update('crypto_topup:300')
        await crypto_amount_selected(update, _context())

        answer_query.assert_awaited_once()
        args = answer_query.call_args.args
        self.assertIn('Ошибка', args[1])

    @patch('apps.telegram_bot.handlers.top_up_cryptobot.render_screen', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.top_up_cryptobot.create_usdt_invoice', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.top_up_cryptobot.get_user', new_callable=AsyncMock)
    async def test_success_renders_screen_with_pay_url_button(self, get_user, create_invoice, render_screen):
        get_user.return_value = self.user
        create_invoice.return_value = {
            'invoice_id': 77,
            'mini_app_invoice_url': 'https://t.me/CryptoBot?start=abc123',
            'bot_invoice_url': 'https://t.me/CryptoBot?start=abc123',
        }

        update = _callback_update('crypto_topup:300')
        await crypto_amount_selected(update, _context())

        render_screen.assert_awaited_once()
        _text, keyboard = render_screen.call_args.args[2], render_screen.call_args.args[3]

        url_buttons = [
            btn
            for row in keyboard.inline_keyboard
            for btn in row
            if btn.url
        ]
        self.assertEqual(len(url_buttons), 1)
        self.assertEqual(url_buttons[0].url, 'https://t.me/CryptoBot?start=abc123')
        self.assertIn('Оплатить', url_buttons[0].text)

    @patch('apps.telegram_bot.handlers.top_up_cryptobot.render_screen', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.top_up_cryptobot.create_usdt_invoice', new_callable=AsyncMock)
    @patch('apps.telegram_bot.handlers.top_up_cryptobot.get_user', new_callable=AsyncMock)
    async def test_success_creates_invoice_row(self, get_user, create_invoice, render_screen):
        from apps.payments.models import CryptoBotInvoice

        get_user.return_value = self.user
        create_invoice.return_value = {
            'invoice_id': 78,
            'mini_app_invoice_url': 'https://t.me/CryptoBot?start=xyz',
            'bot_invoice_url': 'https://t.me/CryptoBot?start=xyz',
        }

        update = _callback_update('crypto_topup:500')
        await crypto_amount_selected(update, _context())

        invoice = await CryptoBotInvoice.objects.aget(invoice_id=78)
        self.assertFalse(invoice.paid)
        self.assertEqual(invoice.amount_rub, Decimal('500.00'))
        self.assertEqual(invoice.user_id, self.user.id)


@override_settings(CRYPTOBOT_TOKEN='')
class BalanceForkAbsentTests(TestCase):
    async def test_crypto_button_absent_when_token_empty(self):
        from apps.telegram_bot.inline_buttons.balance import get_reply_markup_balance

        user = SimpleNamespace(id=99, telegram_id=9901)

        with patch('apps.telegram_bot.inline_buttons.balance.Transaction') as mock_txn:
            mock_txn.objects.filter_by_user.return_value.filter_by_source.return_value.aexists = AsyncMock(
                return_value=True
            )
            keyboard = await get_reply_markup_balance(user)

        button_data = [
            btn.callback_data
            for row in keyboard.inline_keyboard
            for btn in row
            if btn.callback_data
        ]
        self.assertNotIn('crypto_topup', button_data)
