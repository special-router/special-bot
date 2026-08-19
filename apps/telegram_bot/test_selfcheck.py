import re
from decimal import Decimal

from asgiref.sync import sync_to_async
from django.test import TestCase
from django.utils import timezone

from apps.monitoring.models import MonitorState
from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.payments.models import Transaction
from apps.servers.models import Server, TariffServer
from apps.subscriptions.models import SubscriptionDevice
from apps.telegram_bot.handlers.selfcheck import build_selfcheck_screen
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


# Формы, которых не должно быть на экране ни при каких обстоятельствах:
# hostname/IP-подобные строки и инженерные термины из деталей проб.
_HOST_LIKE = re.compile(r'\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b|\b\d{1,3}(?:\.\d{1,3}){3}\b', re.IGNORECASE)
_ENGINEERING_TERMS = ('error_class', 'hwid', 'TCP', 'handshake')


class SelfcheckScreenTests(TestCase):
    def setUp(self):
        self.tariff = TariffServer.objects.create(name='сутки', price=Decimal('7.00'))
        self.server = Server.objects.create(
            name='NL', ip_address='192.0.2.10', ssh_username='x', ssh_password='x',
            vpn_username='x', vpn_password='x', vpn_key='x', inbound_id=5, tariff=self.tariff,
        )
        self.user = TelegramUser.objects.create(telegram_id=5001, username='client')

    def _credit(self, amount: Decimal):
        Transaction.objects.create(
            user=self.user, amount=amount, status=TransactionStatusChoices.SUCCESS,
            source=TransactionSourceChoices.MANUAL,
        )

    def _subscription(self, enabled=True):
        return UserVPN.objects.create(user=self.user, server=self.server, enabled=enabled)

    async def test_active_subscription_shows_days_from_pricing_not_views_formula(self):
        def arrange():
            self._credit(Decimal('35.00'))
            self._subscription()
        await sync_to_async(arrange)()

        text, _ = await build_selfcheck_screen(self.user)

        self.assertIn('на 5 дней', text)
        self.assertNotIn('не работает', text)

    async def test_inactive_subscription_shows_a_clear_message_not_all_good(self):
        def arrange():
            self._credit(Decimal('35.00'))
            self._subscription(enabled=False)
        await sync_to_async(arrange)()

        text, _ = await build_selfcheck_screen(self.user)

        self.assertIn('Подписка не работает', text)
        self.assertNotIn('на 5 дней', text)

    async def test_zero_balance_shows_the_same_clear_message(self):
        await sync_to_async(self._subscription)()

        text, _ = await build_selfcheck_screen(self.user)

        self.assertIn('Подписка не работает', text)

    async def test_devices_distinguish_fresh_from_stale_contact(self):
        def arrange():
            self._credit(Decimal('35.00'))
            subscription = self._subscription()
            SubscriptionDevice.objects.create(
                subscription=subscription, hwid='fresh-000000000', device_model='iPhone',
            )
            stale = SubscriptionDevice.objects.create(
                subscription=subscription, hwid='stale-000000000', device_model='Android',
            )
            SubscriptionDevice.objects.filter(id=stale.id).update(
                last_seen_at=timezone.now() - timezone.timedelta(days=3))
        await sync_to_async(arrange)()

        text, _ = await build_selfcheck_screen(self.user)

        self.assertIn('меньше часа назад', text)
        self.assertIn('3 дн. назад', text)
        self.assertIn('обновите подписку вручную', text)

    async def test_no_devices_is_stated_explicitly(self):
        def arrange():
            self._credit(Decimal('35.00'))
            self._subscription()
        await sync_to_async(arrange)()

        text, _ = await build_selfcheck_screen(self.user)

        self.assertIn('Устройств не привязано', text)

    async def test_all_endpoints_alive_and_fresh(self):
        def arrange():
            self._credit(Decimal('35.00'))
            self._subscription()
            MonitorState.objects.create(
                layer='l1', last_ok=True,
                details={'probe_region': 'ru-bot', 'endpoints': [
                    {'name': 'relay', 'target_region': 'ru-relay', 'port': 443,
                     'transport': 'vless-reality-tcp', 'ok': True, 'latency_ms': 12.5, 'error_class': None},
                ]},
            )
        await sync_to_async(arrange)()

        text, _ = await build_selfcheck_screen(self.user)

        self.assertIn('Точка 1: работает', text)
        self.assertNotIn('недоступна', text)

    async def test_one_endpoint_down_is_shown_in_plain_language(self):
        def arrange():
            self._credit(Decimal('35.00'))
            self._subscription()
            MonitorState.objects.create(
                layer='l1', last_ok=False,
                details={'probe_region': 'ru-bot', 'endpoints': [
                    {'name': 'relay', 'target_region': 'ru-relay', 'port': 443,
                     'transport': 'vless-reality-tcp', 'ok': False, 'latency_ms': None, 'error_class': 'TimeoutError'},
                    {'name': 'direct', 'target_region': 'nl', 'port': 443,
                     'transport': 'vless-reality-tcp', 'ok': True, 'latency_ms': 20.0, 'error_class': None},
                ]},
            )
        await sync_to_async(arrange)()

        text, _ = await build_selfcheck_screen(self.user)

        self.assertIn('Точка 1: недоступна', text)
        self.assertIn('Точка 2: работает', text)
        self.assertIn('попробуйте выбрать другой сервер', text)
        self.assertNotIn('TimeoutError', text)

    async def test_no_monitoring_configured_says_so_explicitly(self):
        def arrange():
            self._credit(Decimal('35.00'))
            self._subscription()
        await sync_to_async(arrange)()

        text, _ = await build_selfcheck_screen(self.user)

        self.assertIn('не проверяем это автоматически', text)
        self.assertNotIn('Точка 1', text)

    async def test_empty_endpoint_list_says_the_same_thing(self):
        def arrange():
            self._credit(Decimal('35.00'))
            self._subscription()
            MonitorState.objects.create(layer='l1', last_ok=True, details={'probe_region': 'ru-bot', 'endpoints': []})
        await sync_to_async(arrange)()

        text, _ = await build_selfcheck_screen(self.user)

        self.assertIn('не проверяем это автоматически', text)

    async def test_stale_monitoring_data_reads_as_unknown_not_alive(self):
        def arrange():
            self._credit(Decimal('35.00'))
            self._subscription()
            state = MonitorState.objects.create(
                layer='l1', last_ok=True,
                details={'probe_region': 'ru-bot', 'endpoints': [
                    {'name': 'relay', 'target_region': 'ru-relay', 'port': 443,
                     'transport': 'vless-reality-tcp', 'ok': True, 'latency_ms': 12.5, 'error_class': None},
                ]},
            )
            MonitorState.objects.filter(id=state.id).update(
                checked_at=timezone.now() - timezone.timedelta(minutes=45))
        await sync_to_async(arrange)()

        text, _ = await build_selfcheck_screen(self.user)

        self.assertIn('не знаем текущее состояние', text)
        self.assertNotIn('Точка 1: работает', text)

    async def test_router_data_line_is_always_present_and_honest(self):
        def arrange():
            self._credit(Decimal('35.00'))
            self._subscription()
        await sync_to_async(arrange)()

        text, _ = await build_selfcheck_screen(self.user)

        self.assertIn('роутера', text)
        self.assertIn('не подключена', text)

    async def test_screen_never_leaks_subscription_identifiers_or_hosts(self):
        def arrange():
            self._credit(Decimal('35.00'))
            subscription = self._subscription()
            # sub_id is blank by default; give it a real-shaped value so the
            # assertion below actually exercises the leak it is meant to catch.
            subscription.sub_id = 'a1b2c3d4e5f6a1b2c3d4e5f6'
            subscription.save(update_fields=['sub_id'])
            SubscriptionDevice.objects.create(subscription=subscription, hwid='fresh-000000000')
            MonitorState.objects.create(
                layer='l1', last_ok=False,
                details={'probe_region': 'ru-bot', 'endpoints': [
                    {'name': 'relay', 'target_region': 'ru-relay', 'port': 443,
                     'transport': 'vless-reality-tcp', 'ok': False, 'latency_ms': None, 'error_class': 'OSError'},
                ]},
            )
            return subscription
        subscription = await sync_to_async(arrange)()

        text, _ = await build_selfcheck_screen(self.user)

        self.assertNotIn(subscription.sub_id, text)
        self.assertNotIn(str(subscription.vpn_uuid), text)
        self.assertIsNone(_HOST_LIKE.search(text), msg=f'host-like token leaked into: {text!r}')

    async def test_screen_never_uses_engineering_vocabulary(self):
        def arrange():
            self._credit(Decimal('35.00'))
            subscription = self._subscription()
            MonitorState.objects.create(
                layer='l1', last_ok=False,
                details={'probe_region': 'ru-bot', 'endpoints': [
                    {'name': 'relay', 'target_region': 'ru-relay', 'port': 443,
                     'transport': 'vless-reality-tcp', 'ok': False, 'latency_ms': None, 'error_class': 'OSError'},
                ]},
            )
            SubscriptionDevice.objects.create(subscription=subscription, hwid='fresh-000000000')
        await sync_to_async(arrange)()

        text, _ = await build_selfcheck_screen(self.user)

        for term in _ENGINEERING_TERMS:
            self.assertNotIn(term, text)
