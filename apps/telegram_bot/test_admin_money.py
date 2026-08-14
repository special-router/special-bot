"""Money and provisioning actions: balance credit, VPN issue, VPN disable.

Every execute handler here re-verifies state immediately before acting; the
"already gone" tests below are what actually exercises that, not just the
happy path.
"""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import sync_to_async
from django.test import TestCase, override_settings

from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.payments.models import Transaction
from apps.servers.models import Server, TariffServer
from apps.telegram_bot.handlers.admin.common import AWAITING_BALANCE_AMOUNT, AWAITING_KEY
from apps.telegram_bot.handlers.admin.money import (
    admin_credit_execute,
    admin_credit_start,
    admin_vpn_disable_confirm,
    admin_vpn_disable_execute,
    admin_vpn_issue_execute,
    admin_vpn_issue_start,
    handle_amount_text,
)
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


ADMIN_ID = 9001


def _server(name='Нидерланды', price='7.00'):
    tariff = TariffServer.objects.create(name='Базовый', price=Decimal(price))
    return Server.objects.create(
        name=name, ip_address='192.0.2.1', ssh_username='x', ssh_password='x',
        vpn_username='x', vpn_password='x', vpn_key='x', tariff=tariff,
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


def _text_update(text, telegram_id=ADMIN_ID):
    from_user = SimpleNamespace(id=telegram_id, username='op')
    return SimpleNamespace(
        callback_query=None, message=SimpleNamespace(text=text, from_user=from_user),
        effective_chat=SimpleNamespace(id=telegram_id), effective_user=from_user,
    )


def _context():
    return SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()), user_data={})


@override_settings(BOT_ADMIN_TELEGRAM_IDS=[ADMIN_ID])
class BalanceCreditTests(TestCase):
    def setUp(self):
        self.user = TelegramUser.objects.create(telegram_id=1001, username='client')

    async def test_a_valid_amount_reaches_the_confirm_screen(self):
        context = _context()
        await admin_credit_start(_callback_update(f'admin_credit:{self.user.id}'), context)

        await handle_amount_text(_text_update('250'), context)

        self.assertEqual(context.user_data['admin_credit_pending_amount'], '250.00')
        self.assertNotIn(AWAITING_KEY, context.user_data)

    async def test_an_unparsable_amount_reprompts_without_moving_on(self):
        context = _context()
        await admin_credit_start(_callback_update(f'admin_credit:{self.user.id}'), context)

        await handle_amount_text(_text_update('не число'), context)

        self.assertEqual(context.user_data.get(AWAITING_KEY), AWAITING_BALANCE_AMOUNT)
        self.assertNotIn('admin_credit_pending_amount', context.user_data)

    async def test_confirming_creates_exactly_one_manual_transaction(self):
        context = _context()
        await admin_credit_start(_callback_update(f'admin_credit:{self.user.id}'), context)
        await handle_amount_text(_text_update('250'), context)

        await admin_credit_execute(_callback_update('admin_credit_execute'), context)

        transactions = [t async for t in Transaction.objects.filter(user=self.user)]
        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0].source, TransactionSourceChoices.MANUAL)
        self.assertEqual(transactions[0].status, TransactionStatusChoices.SUCCESS)
        self.assertEqual(transactions[0].amount, Decimal('250.00'))

    async def test_executing_twice_without_a_new_prompt_only_credits_once(self):
        context = _context()
        await admin_credit_start(_callback_update(f'admin_credit:{self.user.id}'), context)
        await handle_amount_text(_text_update('250'), context)

        await admin_credit_execute(_callback_update('admin_credit_execute'), context)
        await admin_credit_execute(_callback_update('admin_credit_execute'), context)

        self.assertEqual(await Transaction.objects.filter(user=self.user).acount(), 1)

    async def test_a_non_admin_gets_no_response(self):
        update = _callback_update(f'admin_credit:{self.user.id}', telegram_id=4242)

        await admin_credit_start(update, _context())

        update.callback_query.edit_message_text.assert_not_awaited()


@override_settings(BOT_ADMIN_TELEGRAM_IDS=[ADMIN_ID])
class VpnIssueTests(TestCase):
    def setUp(self):
        self.user = TelegramUser.objects.create(telegram_id=1001, username='client')
        self.server = _server()

    async def _issue(self, context, callback_data):
        with patch('apps.vpn.services.add_vpn_to_user.APIVPNClient') as client:
            client.return_value.enable_user = AsyncMock()
            client.return_value.get_key = AsyncMock(return_value='vless://key')
            await admin_vpn_issue_execute(_callback_update(callback_data), context)

    async def test_issuing_creates_one_enabled_subscription(self):
        context = _context()

        await self._issue(context, f'admin_vpn_issue_execute:{self.user.id}:{self.server.id}')

        subscriptions = [vpn async for vpn in UserVPN.objects.filter(user=self.user)]
        self.assertEqual(len(subscriptions), 1)
        self.assertTrue(subscriptions[0].enabled)

    async def test_issuing_against_an_existing_disabled_subscription_re_enables_it_not_duplicates_it(self):
        existing = await UserVPN.objects.acreate(user=self.user, server=self.server, enabled=False)
        context = _context()

        await self._issue(context, f'admin_vpn_issue_execute:{self.user.id}:{self.server.id}')

        self.assertEqual(await UserVPN.objects.filter(user=self.user).acount(), 1)
        await existing.arefresh_from_db()
        self.assertTrue(existing.enabled)

    async def test_a_vanished_server_is_refused_at_execute_time(self):
        update = _callback_update(f'admin_vpn_issue_execute:{self.user.id}:999999')

        await admin_vpn_issue_execute(update, _context())

        update.callback_query.answer.assert_awaited_once()
        self.assertFalse(await UserVPN.objects.filter(user=self.user).aexists())

    async def test_start_with_no_servers_answers_a_toast_and_stops(self):
        await sync_to_async(Server.objects.all().delete)()
        update = _callback_update(f'admin_vpn_issue:{self.user.id}')

        await admin_vpn_issue_start(update, _context())

        update.callback_query.answer.assert_awaited_once()
        update.callback_query.edit_message_text.assert_not_awaited()


@override_settings(BOT_ADMIN_TELEGRAM_IDS=[ADMIN_ID])
class VpnDisableTests(TestCase):
    def setUp(self):
        self.user = TelegramUser.objects.create(telegram_id=1001, username='client')
        self.server = _server()

    async def test_disabling_keeps_the_row_and_flips_enabled_false(self):
        subscription = await UserVPN.objects.acreate(user=self.user, server=self.server, enabled=True)
        context = _context()

        with patch('apps.vpn.services.remove_vpn_user_from_server.APIVPNClient') as client:
            client.return_value.enable_user = AsyncMock()
            await admin_vpn_disable_execute(_callback_update(f'admin_vpn_disable_execute:{subscription.id}'), context)

        self.assertEqual(await UserVPN.objects.filter(user=self.user).acount(), 1)
        await subscription.arefresh_from_db()
        self.assertFalse(subscription.enabled)

    async def test_disabling_an_already_disabled_subscription_is_refused(self):
        subscription = await UserVPN.objects.acreate(user=self.user, server=self.server, enabled=False)
        update = _callback_update(f'admin_vpn_disable_execute:{subscription.id}')

        await admin_vpn_disable_execute(update, _context())

        update.callback_query.answer.assert_awaited_once()

    async def test_confirm_screen_for_a_missing_subscription_is_refused(self):
        update = _callback_update('admin_vpn_disable:999999')

        await admin_vpn_disable_confirm(update, _context())

        update.callback_query.answer.assert_awaited_once()
        update.callback_query.edit_message_text.assert_not_awaited()

    async def test_a_non_admin_gets_no_response(self):
        subscription = await UserVPN.objects.acreate(user=self.user, server=self.server, enabled=True)
        update = _callback_update(f'admin_vpn_disable_execute:{subscription.id}', telegram_id=4242)

        await admin_vpn_disable_execute(update, _context())

        update.callback_query.answer.assert_not_awaited()
        await subscription.arefresh_from_db()
        self.assertTrue(subscription.enabled)
