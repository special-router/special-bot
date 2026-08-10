from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import AsyncMock, patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.test import RequestFactory, TestCase
from django.utils import timezone
from telegram.error import BadRequest, NetworkError, RetryAfter

from apps.payments.models import Transaction
from apps.servers.models import Server, TariffServer
from apps.telegram_bot.admin import BroadcastAdmin
from apps.telegram_bot.models import Broadcast, BroadcastDelivery
from apps.telegram_bot.tasks import _claim_delivery, _reply_markup, safe_broadcast_v1
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


class BroadcastTestCase(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username='admin')
        self.tariff = TariffServer.objects.create(name='Test', price=Decimal('10.00'))
        self.expensive_tariff = TariffServer.objects.create(name='Expensive', price=Decimal('20.00'))
        self.server = self.server_for(self.tariff, 'Test')
        self.expensive_server = self.server_for(self.expensive_tariff, 'Expensive')

    def server_for(self, tariff, name):
        return Server.objects.create(
            name=name, ip_address=f'127.0.0.{tariff.pk}', ssh_username='x', ssh_password='x',
            vpn_username='x', vpn_password='x', vpn_key='x', tariff=tariff,
        )

    def user_vpn(self, telegram_id, *, sub_id='ready', server=None, balance=Decimal('0.00')):
        user = TelegramUser.objects.create(telegram_id=telegram_id, username=f'user-{telegram_id}')
        UserVPN.objects.create(user=user, server=server or self.server, sub_id=sub_id, enabled=False)
        if balance:
            Transaction.objects.create(user=user, amount=balance, status='SUCCESS')
        return user

    def broadcast(self, **kwargs):
        defaults = {'title': 'Announcement', 'message': 'A safe announcement', 'created_by': self.owner}
        defaults.update(kwargs)
        return Broadcast.objects.create(**defaults)

    def test_subscription_ready_requires_the_same_vpn_for_sub_id_and_tariff(self):
        mixed = self.user_vpn(1, sub_id='ready-but-expensive', server=self.expensive_server, balance=Decimal('10.00'))
        UserVPN.objects.create(user=mixed, server=self.server, sub_id='')
        entitled = self.user_vpn(2, sub_id='ready-cheap', balance=Decimal('10.00'))

        self.assertEqual(list(Broadcast.subscription_ready_recipients()), [entitled])

    def test_subscription_ready_excludes_duplicate_sub_ids_and_duplicate_telegram_ids(self):
        self.user_vpn(1, sub_id='duplicate', balance=Decimal('10.00'))
        self.user_vpn(2, sub_id='duplicate', balance=Decimal('10.00'))
        canonical = self.user_vpn(3, sub_id='canonical', balance=Decimal('10.00'))
        duplicate_row = TelegramUser.objects.create(telegram_id=3, username='duplicate-row')
        UserVPN.objects.create(user=duplicate_row, server=self.server, sub_id='other', enabled=False)
        Transaction.objects.create(user=duplicate_row, amount=Decimal('10.00'), status='SUCCESS')

        recipients = Broadcast.subscription_ready_recipients()

        self.assertEqual(list(recipients), [canonical])
        self.assertEqual(self.broadcast(audience=Broadcast.AUDIENCE_ALL).recipient_queryset().count(), 3)

    def test_callback_markup_contains_no_subscription_value(self):
        broadcast = self.broadcast(include_subscription_button=True)
        self.assertEqual(_reply_markup(broadcast).inline_keyboard[0][0].callback_data, 'show_keys')
        self.assertIsNone(_reply_markup(self.broadcast(include_subscription_button=False)))

    def test_failed_legacy_broadcast_without_ledger_is_not_resumable(self):
        broadcast = self.broadcast(status='failed')
        self.assertFalse(broadcast.can_be_sent())

    @patch('apps.telegram_bot.tasks.time.sleep')
    @patch('apps.telegram_bot.tasks.Bot')
    def test_definitive_error_is_failed_and_network_error_is_uncertain(self, bot_class, sleep):
        permanent = self.user_vpn(10, sub_id='permanent', balance=Decimal('10.00'))
        uncertain = self.user_vpn(11, sub_id='uncertain', balance=Decimal('10.00'))
        broadcast = self.broadcast(status='queued')
        BroadcastDelivery.objects.bulk_create([
            BroadcastDelivery(broadcast=broadcast, user=permanent),
            BroadcastDelivery(broadcast=broadcast, user=uncertain),
        ])
        bot = bot_class.return_value
        bot.initialize = AsyncMock()
        bot.shutdown = AsyncMock()
        bot.send_message = AsyncMock(side_effect=[BadRequest('invalid'), NetworkError('network')])

        safe_broadcast_v1.run(broadcast.id)

        statuses = dict(BroadcastDelivery.objects.filter(broadcast=broadcast).values_list('user_id', 'status'))
        self.assertEqual(statuses[permanent.id], 'failed')
        self.assertEqual(statuses[uncertain.id], 'sending')
        self.assertEqual(bot.send_message.await_count, 2)

    @patch('apps.telegram_bot.tasks.time.sleep')
    @patch('apps.telegram_bot.tasks.Bot')
    def test_retry_after_retries_the_same_delivery_without_external_calls(self, bot_class, sleep):
        self.user_vpn(20, sub_id='retry', balance=Decimal('10.00'))
        broadcast = self.broadcast(status='queued')
        BroadcastDelivery.objects.create(broadcast=broadcast, user=TelegramUser.objects.get(telegram_id=20))
        bot = bot_class.return_value
        bot.initialize = AsyncMock()
        bot.shutdown = AsyncMock()
        bot.send_message = AsyncMock(side_effect=[RetryAfter(timedelta(seconds=2.1)), None])

        safe_broadcast_v1.run(broadcast.id)

        delivery = BroadcastDelivery.objects.get(broadcast=broadcast)
        self.assertEqual(delivery.status, 'sent')
        self.assertEqual(delivery.attempt_count, 1)
        self.assertEqual(bot.send_message.await_count, 2)
        sleep.assert_any_call(3)

    @patch('apps.telegram_bot.tasks.time.sleep')
    @patch('apps.telegram_bot.tasks.Bot')
    def test_retry_after_does_not_send_after_stale_recovery_revokes_lease(self, bot_class, sleep):
        user = self.user_vpn(21, sub_id='retry-stale', balance=Decimal('10.00'))
        broadcast = self.broadcast(status='queued')
        delivery = BroadcastDelivery.objects.create(broadcast=broadcast, user=user)
        bot = bot_class.return_value
        bot.initialize = AsyncMock()
        bot.shutdown = AsyncMock()
        bot.send_message = AsyncMock(side_effect=RetryAfter(timedelta(seconds=1)))

        def revoke_lease(_seconds):
            Broadcast.objects.filter(pk=broadcast.pk).update(status='failed')

        sleep.side_effect = revoke_lease
        safe_broadcast_v1.run(broadcast.id)

        self.assertEqual(bot.send_message.await_count, 1)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, BroadcastDelivery.STATUS_SENDING)

    def test_direct_task_does_not_snapshot_or_send_a_draft(self):
        self.user_vpn(21, sub_id='draft', balance=Decimal('10.00'))
        broadcast = self.broadcast()
        safe_broadcast_v1.run(broadcast.id)
        self.assertEqual(broadcast.status, 'draft')
        self.assertFalse(BroadcastDelivery.objects.filter(broadcast=broadcast).exists())

    def test_subscription_ready_ignores_unsuccessful_transaction_amounts(self):
        user = self.user_vpn(22, sub_id='not-paid', balance=Decimal('0.00'))
        Transaction.objects.create(user=user, amount=Decimal('100.00'), status='FAILED')
        Transaction.objects.create(user=user, amount=Decimal('100.00'), status='PENDING')
        self.assertFalse(Broadcast.subscription_ready_recipients().filter(pk=user.pk).exists())

    def test_delivery_claim_requires_sending_parent_and_updates_heartbeat(self):
        user = self.user_vpn(23, sub_id='lease', balance=Decimal('10.00'))
        broadcast = self.broadcast(status='queued')
        delivery = BroadcastDelivery.objects.create(broadcast=broadcast, user=user)
        self.assertIsNone(_claim_delivery(broadcast.id))
        broadcast.status = 'sending'
        broadcast.heartbeat_at = timezone.now() - timedelta(minutes=31)
        broadcast.save(update_fields=['status', 'heartbeat_at'])
        self.assertEqual(_claim_delivery(broadcast.id).pk, delivery.pk)
        broadcast.refresh_from_db()
        self.assertGreater(broadcast.heartbeat_at, timezone.now() - timedelta(minutes=1))

    def test_duplicate_requires_add_permission_in_addition_to_change(self):
        request = RequestFactory().post('/admin/telegram_bot/broadcast/')
        request.user = get_user_model().objects.create_user(username='change-only')
        request.user.user_permissions.add(Permission.objects.get(codename='change_broadcast'))
        broadcast = self.broadcast()
        admin_view = BroadcastAdmin(Broadcast, admin.site)
        with self.assertRaises(Exception):
            admin_view.duplicate_broadcast(request, Broadcast.objects.filter(pk=broadcast.pk))
        self.assertEqual(Broadcast.objects.count(), 1)

    def test_message_length_validation(self):
        invalid = Broadcast(title='Bad', message='x' * 4097, created_by=self.owner)
        with self.assertRaises(Exception):
            invalid.full_clean()

    def test_dry_run_command_outputs_aggregate_count_only(self):
        self.user_vpn(30, sub_id='command', balance=Decimal('10.00'))
        output = StringIO()
        call_command('broadcast_audience_count', stdout=output)
        self.assertEqual(output.getvalue(), '1\n')
