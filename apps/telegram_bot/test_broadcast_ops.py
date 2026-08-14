"""Guarantees `broadcast_ops` makes for both callers: Django admin and the bot.

Two things matter here and nowhere else: the recipient snapshot is frozen
before anyone can confirm it, and a confirmation is refused the moment the
digest it carries stops matching the current row — whether that is because
someone edited the broadcast, or because a caller replays an old digest.
`test_broadcast.py` already covers the celery delivery task in isolation; this
file covers the state machine both admin surfaces share.
"""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.telegram_bot import broadcast_ops
from apps.telegram_bot.models import Broadcast, BroadcastDelivery
from apps.users.models import TelegramUser


class BroadcastOpsTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username='admin')
        TelegramUser.objects.create(telegram_id=1, username='one')
        TelegramUser.objects.create(telegram_id=2, username='two')

    def _draft(self, **overrides):
        fields = dict(title='Заголовок', message='Текст рассылки достаточной длины', audience=Broadcast.AUDIENCE_ALL)
        fields.update(overrides)
        return Broadcast.objects.create(created_by=self.owner, **fields)

    def test_create_preview_snapshot_freezes_the_current_recipients(self):
        broadcast = self._draft()

        snapshot = broadcast_ops.create_preview_snapshot(broadcast.id)

        self.assertEqual(snapshot.status, 'confirming')
        self.assertEqual(snapshot.total_users, 2)
        self.assertEqual(BroadcastDelivery.objects.filter(broadcast=snapshot).count(), 2)

    def test_create_preview_snapshot_refuses_a_broadcast_that_is_not_a_draft(self):
        broadcast = self._draft(status='queued')

        self.assertIsNone(broadcast_ops.create_preview_snapshot(broadcast.id))

    def test_digest_changes_when_the_frozen_snapshot_would_no_longer_match(self):
        broadcast = broadcast_ops.create_preview_snapshot(self._draft().id)
        original_digest = broadcast_ops.confirmation_digest(broadcast)

        broadcast.message = 'Изменённый текст рассылки той же длины'
        broadcast.save(update_fields=['message'])

        self.assertNotEqual(original_digest, broadcast_ops.confirmation_digest(broadcast))

    @patch('apps.telegram_bot.tasks.safe_broadcast_v1.delay')
    def test_queue_confirmed_broadcast_accepts_a_current_digest(self, delay):
        broadcast = broadcast_ops.create_preview_snapshot(self._draft().id)
        digest = broadcast_ops.confirmation_digest(broadcast)

        result = broadcast_ops.queue_confirmed_broadcast(broadcast.id, digest)

        self.assertTrue(result.ok)
        broadcast.refresh_from_db()
        self.assertEqual(broadcast.status, 'queued')

    def test_queue_confirmed_broadcast_rejects_a_stale_digest(self):
        broadcast = broadcast_ops.create_preview_snapshot(self._draft().id)
        digest = broadcast_ops.confirmation_digest(broadcast)
        broadcast.message = 'Другой текст рассылки той же длины символов'
        broadcast.save(update_fields=['message'])

        result = broadcast_ops.queue_confirmed_broadcast(broadcast.id, digest)

        self.assertFalse(result.ok)
        self.assertEqual(result.error, 'stale')
        broadcast.refresh_from_db()
        self.assertEqual(broadcast.status, 'confirming')

    def test_queue_confirmed_broadcast_rejects_a_tampered_digest(self):
        broadcast = broadcast_ops.create_preview_snapshot(self._draft().id)

        result = broadcast_ops.queue_confirmed_broadcast(broadcast.id, 'not-a-real-digest')

        self.assertFalse(result.ok)
        self.assertEqual(result.error, 'stale')

    def test_queue_confirmed_broadcast_detects_a_corrupted_snapshot(self):
        broadcast = broadcast_ops.create_preview_snapshot(self._draft().id)
        digest = broadcast_ops.confirmation_digest(broadcast)
        BroadcastDelivery.objects.filter(broadcast=broadcast).first().delete()

        result = broadcast_ops.queue_confirmed_broadcast(broadcast.id, digest)

        self.assertFalse(result.ok)
        self.assertEqual(result.error, 'snapshot_corrupt')

    def test_cancel_confirming_broadcast_returns_to_draft_and_drops_the_snapshot(self):
        broadcast = broadcast_ops.create_preview_snapshot(self._draft().id)

        self.assertTrue(broadcast_ops.cancel_confirming_broadcast(broadcast.id))

        broadcast.refresh_from_db()
        self.assertEqual(broadcast.status, 'draft')
        self.assertIsNone(broadcast.preview_snapshot_id)
        self.assertEqual(BroadcastDelivery.objects.filter(broadcast=broadcast).count(), 0)

    def test_cancel_confirming_broadcast_is_a_no_op_off_the_confirming_status(self):
        broadcast = self._draft()

        self.assertFalse(broadcast_ops.cancel_confirming_broadcast(broadcast.id))


class BroadcastAdminActionTests(TestCase):
    """The Django admin action still exercises the real, now-shared, state machine."""

    def setUp(self):
        self.owner = get_user_model().objects.create_superuser('operator', 'operator@example.test', 'password')
        self.client.force_login(self.owner)
        TelegramUser.objects.create(telegram_id=1, username='one')
        TelegramUser.objects.create(telegram_id=2, username='two')
        self.broadcast = Broadcast.objects.create(
            created_by=self.owner, title='Заголовок', message='Текст рассылки достаточной длины',
            audience=Broadcast.AUDIENCE_ALL,
        )
        self.changelist_url = reverse('admin:telegram_bot_broadcast_changelist')

    def _post(self, data):
        payload = {'action': 'send_broadcast', '_selected_action': [self.broadcast.pk]}
        payload.update(data)
        return self.client.post(self.changelist_url, payload, follow=True)

    def test_the_first_post_snapshots_recipients_and_asks_to_confirm(self):
        response = self._post({})

        self.assertEqual(response.status_code, 200)
        self.broadcast.refresh_from_db()
        self.assertEqual(self.broadcast.status, 'confirming')
        self.assertEqual(self.broadcast.total_users, 2)

    @patch('apps.telegram_bot.tasks.safe_broadcast_v1.delay')
    def test_confirming_with_the_right_digest_queues_it(self, delay):
        self._post({})
        self.broadcast.refresh_from_db()
        digest = broadcast_ops.confirmation_digest(self.broadcast)

        response = self._post({'post': 'yes', 'confirmation_digest': digest})

        self.assertEqual(response.status_code, 200)
        self.broadcast.refresh_from_db()
        self.assertEqual(self.broadcast.status, 'queued')

    def test_confirming_with_a_stale_digest_is_refused(self):
        self._post({})

        response = self._post({'post': 'yes', 'confirmation_digest': 'wrong'})

        self.assertIn('изменились', response.content.decode())
        self.broadcast.refresh_from_db()
        self.assertEqual(self.broadcast.status, 'confirming')

    def test_cancel_returns_the_draft_and_drops_the_snapshot(self):
        self._post({})

        response = self._post({'cancel': 'yes'})

        self.assertEqual(response.status_code, 200)
        self.broadcast.refresh_from_db()
        self.assertEqual(self.broadcast.status, 'draft')
        self.assertEqual(BroadcastDelivery.objects.filter(broadcast=self.broadcast).count(), 0)
