from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.servers.models import Server, TariffServer
from apps.subscriptions.models import SubscriptionAccessToken
from apps.subscriptions.tokens import (
    issue_access_token,
    resolve_access_token,
    revoke_access_tokens,
    rotate_access_token,
)
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


class SubscriptionAccessTokenTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        tariff = TariffServer.objects.create(name='token-test', price='7.00')
        server = Server.objects.create(
            name='token-test',
            ip_address='198.51.100.20',
            ssh_username='ssh',
            ssh_password='ssh',
            vpn_username='panel',
            vpn_password='panel',
            vpn_key='key',
            tariff=tariff,
            inbound_id=5,
        )
        user = TelegramUser.objects.create(telegram_id=9292, username='token-test')
        cls.subscription = UserVPN.objects.create(user=user, server=server, sub_id='legacy-alias')

    def test_issue_stores_only_digest(self):
        raw_token, record = issue_access_token(self.subscription)

        self.assertTrue(raw_token.startswith('sp1_'))
        self.assertNotEqual(record.token_hash, raw_token)
        self.assertNotIn(raw_token, str(SubscriptionAccessToken.objects.values().get()))
        self.assertEqual(resolve_access_token(raw_token).pk, record.pk)

    def test_disabled_subscription_refuses_valid_token(self):
        raw_token, _record = issue_access_token(self.subscription)
        UserVPN.objects.filter(pk=self.subscription.pk).update(enabled=False)

        self.assertIsNone(resolve_access_token(raw_token))

    def test_disabled_subscription_cannot_receive_new_token(self):
        UserVPN.objects.filter(pk=self.subscription.pk).update(enabled=False)
        self.subscription.enabled = False

        with self.assertRaisesMessage(ValidationError, 'disabled_subscription'):
            issue_access_token(self.subscription)

    def test_expired_token_is_refused(self):
        active_from = timezone.now() - timedelta(hours=2)
        raw_token, _record = issue_access_token(
            self.subscription,
            active_from=active_from,
            expires_at=active_from + timedelta(hours=1),
        )

        self.assertIsNone(resolve_access_token(raw_token))

    def test_rotation_keeps_short_overlap(self):
        old_token, old_record = issue_access_token(self.subscription)

        new_token, new_record = rotate_access_token(self.subscription.id, overlap=timedelta(minutes=5))

        old_record.refresh_from_db()
        self.assertIsNotNone(old_record.expires_at)
        self.assertGreater(old_record.expires_at, timezone.now())
        self.assertEqual(resolve_access_token(old_token).pk, old_record.pk)
        self.assertEqual(resolve_access_token(new_token).pk, new_record.pk)

    def test_immediate_rotation_revokes_previous_token(self):
        old_token, _old_record = issue_access_token(self.subscription)

        new_token, _new_record = rotate_access_token(self.subscription.id, overlap=timedelta(0))

        self.assertIsNone(resolve_access_token(old_token))
        self.assertIsNotNone(resolve_access_token(new_token))

    def test_rotation_overlap_is_bounded(self):
        issue_access_token(self.subscription)

        with self.assertRaisesMessage(ValidationError, 'invalid_access_token_overlap'):
            rotate_access_token(self.subscription.id, overlap=timedelta(hours=2))

    def test_revoke_refuses_all_tokens_for_subscription(self):
        first, _first_record = issue_access_token(self.subscription)
        second, _second_record = issue_access_token(self.subscription)

        updated = revoke_access_tokens(self.subscription.id)

        self.assertEqual(updated, 2)
        self.assertIsNone(resolve_access_token(first))
        self.assertIsNone(resolve_access_token(second))

    def test_touch_records_only_successful_use(self):
        raw_token, record = issue_access_token(self.subscription)

        resolved = resolve_access_token(raw_token, touch=True)

        record.refresh_from_db()
        self.assertEqual(resolved.pk, record.pk)
        self.assertIsNotNone(record.last_seen_at)
