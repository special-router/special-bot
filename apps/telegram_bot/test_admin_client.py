"""The read-only client card: lookup, and what it shows.

This screen never mutates anything — the money and provisioning actions it
links to are covered in `test_admin_money.py`, not here.
"""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from django.test import TestCase, override_settings

from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.payments.models import Transaction
from apps.servers.models import Server, TariffServer
from apps.subscriptions.models import SubscriptionDevice
from apps.telegram_bot.handlers.admin.client import admin_client_view, handle_lookup_text
from apps.telegram_bot.handlers.admin.common import AWAITING_CLIENT_LOOKUP, AWAITING_KEY
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


ADMIN_ID = 9001


def _server(name='Нидерланды', price='7.00'):
    tariff = TariffServer.objects.create(name='Базовый', price=Decimal(price))
    return Server.objects.create(
        name=name, ip_address='192.0.2.1', ssh_username='x', ssh_password='x',
        vpn_username='x', vpn_password='x', vpn_key='x', tariff=tariff,
    )


def _text_update(text, telegram_id=ADMIN_ID):
    from_user = SimpleNamespace(id=telegram_id, username='op')
    return SimpleNamespace(
        callback_query=None, message=SimpleNamespace(text=text, from_user=from_user),
        effective_chat=SimpleNamespace(id=telegram_id), effective_user=from_user,
    )


def _callback_update(data, telegram_id=ADMIN_ID):
    query = SimpleNamespace(
        data=data, from_user=SimpleNamespace(id=telegram_id, username='op'),
        answer=AsyncMock(), edit_message_text=AsyncMock(),
    )
    return SimpleNamespace(
        callback_query=query, message=None,
        effective_chat=SimpleNamespace(id=telegram_id), effective_user=query.from_user,
    )


def _context():
    bot = SimpleNamespace(send_message=AsyncMock())
    return SimpleNamespace(bot=bot, user_data={})


@override_settings(BOT_ADMIN_TELEGRAM_IDS=[ADMIN_ID])
class ClientLookupTests(TestCase):
    def setUp(self):
        self.user = TelegramUser.objects.create(telegram_id=1001, username='client')
        self.subscription = UserVPN.objects.create(user=self.user, server=_server(), enabled=True)
        SubscriptionDevice.objects.create(subscription=self.subscription, hwid='hwid-1', device_model='iPhone')
        SubscriptionDevice.objects.create(subscription=self.subscription, hwid='hwid-2')
        Transaction.objects.create(
            user=self.user, amount=Decimal('120.00'),
            status=TransactionStatusChoices.SUCCESS, source=TransactionSourceChoices.YOUMONEY,
        )

    async def test_lookup_by_username_finds_the_client(self):
        context = _context()

        await handle_lookup_text(_text_update('@client'), context)

        text = context.bot.send_message.await_args.kwargs['text']
        self.assertIn('client', text)
        self.assertIn('Нидерланды', text)

    async def test_lookup_by_numeric_telegram_id_finds_the_client(self):
        context = _context()

        await handle_lookup_text(_text_update('1001'), context)

        context.bot.send_message.assert_awaited_once()

    async def test_lookup_of_an_unknown_client_reports_not_found_and_stays_open(self):
        context = _context()
        context.user_data[AWAITING_KEY] = AWAITING_CLIENT_LOOKUP

        await handle_lookup_text(_text_update('@nobody'), context)

        text = context.bot.send_message.await_args.kwargs['text']
        self.assertIn('не найден', text)

    async def test_the_card_shows_balance_devices_and_never_the_raw_hwid(self):
        context = _context()

        await handle_lookup_text(_text_update('@client'), context)

        text = context.bot.send_message.await_args.kwargs['text']
        self.assertIn('120.00', text)
        self.assertIn('iPhone', text)
        self.assertIn('без названия', text)
        self.assertNotIn('hwid-1', text)
        self.assertNotIn('hwid-2', text)

    async def test_the_card_is_reached_directly_by_id_too(self):
        update = _callback_update(f'admin_client_view:{self.user.id}')

        await admin_client_view(update, _context())

        update.callback_query.edit_message_text.assert_awaited_once()

    async def test_a_non_admin_lookup_view_gets_no_response(self):
        update = _callback_update(f'admin_client_view:{self.user.id}', telegram_id=4242)

        await admin_client_view(update, _context())

        update.callback_query.edit_message_text.assert_not_awaited()
        update.callback_query.answer.assert_not_awaited()
