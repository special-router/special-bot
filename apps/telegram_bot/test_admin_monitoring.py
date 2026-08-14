"""The monitoring dashboard: what it shows, and what it must never show.

`MonitorState.details` is written by `apps.monitoring.probes` and is not
expected to carry secrets today — but the dashboard renders it through a
per-layer allowlist rather than dumping it, so a probe that someday adds a
field it should not still cannot leak it here. That property is what this
file actually tests, with a deliberately secret-shaped value the allowlist
was never told about.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

from django.test import TestCase, override_settings

from apps.monitoring.models import MonitorState, MonitorTransition
from apps.telegram_bot.handlers.admin.monitoring import admin_monitor, admin_monitor_layer


ADMIN_ID = 9001
SECRET = 'https://panel.example.test/9f3a7c-secret-base-path'


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
    return SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()), user_data={})


@override_settings(BOT_ADMIN_TELEGRAM_IDS=[ADMIN_ID])
class MonitorDashboardTests(TestCase):
    def setUp(self):
        MonitorState.objects.create(
            layer='checkout', last_ok=False, alert=True, consecutive_failures=3, error_class='cash_gap',
            details={
                'tariff_ok': True, 'invoice_ok': False, 'cash_gap_days': 5,
                # Not in the layer's allowlist on purpose: a future probe field
                # this dashboard was never told to render.
                'panel_url': SECRET,
            },
        )
        MonitorState.objects.create(
            layer='l0', last_ok=True, alert=False, consecutive_failures=0,
            details={'inbounds': [{'server_id': 1, 'inbound_id': 1}], 'inventory_drift': False},
        )

    async def test_the_dashboard_renders_both_layers(self):
        update = _callback_update('admin_monitor')

        await admin_monitor(update, _context())

        text = update.callback_query.edit_message_text.await_args.kwargs['text']
        self.assertIn('L0', text)
        self.assertIn('Checkout', text)
        self.assertIn('cash_gap', text)

    async def test_an_unallowlisted_details_field_never_reaches_the_screen(self):
        update = _callback_update('admin_monitor')

        await admin_monitor(update, _context())

        text = update.callback_query.edit_message_text.await_args.kwargs['text']
        self.assertNotIn(SECRET, text)
        self.assertNotIn('panel_url', text)

    async def test_a_non_admin_gets_no_response(self):
        update = _callback_update('admin_monitor', telegram_id=4242)

        await admin_monitor(update, _context())

        update.callback_query.edit_message_text.assert_not_awaited()


@override_settings(BOT_ADMIN_TELEGRAM_IDS=[ADMIN_ID])
class MonitorTransitionHistoryTests(TestCase):
    def setUp(self):
        for index in range(12):
            MonitorTransition.objects.create(
                layer='l1', event='opened' if index % 2 == 0 else 'recovered', error_class='regional_reachability',
                consecutive_failures=index,
            )

    async def test_history_is_bounded_to_ten_rows(self):
        update = _callback_update('admin_monitor_layer:l1')

        await admin_monitor_layer(update, _context())

        text = update.callback_query.edit_message_text.await_args.kwargs['text']
        self.assertEqual(text.count('regional_reachability'), 10)

    async def test_history_names_both_kinds_of_transition(self):
        update = _callback_update('admin_monitor_layer:l1')

        await admin_monitor_layer(update, _context())

        text = update.callback_query.edit_message_text.await_args.kwargs['text']
        self.assertIn('открыт', text)
        self.assertIn('восстановлено', text)

    async def test_a_layer_with_no_transitions_says_so(self):
        update = _callback_update('admin_monitor_layer:host')

        await admin_monitor_layer(update, _context())

        text = update.callback_query.edit_message_text.await_args.kwargs['text']
        self.assertIn('Переходов не было', text)
