"""Routing a pending admin's free-text reply, and the filter that gates it.

Every other flow in this bot is button-driven, so nothing else needs a filter
like this one. It must match only an admin who is actually mid-prompt — never
an ordinary customer's message, and never an admin who merely exists.
"""
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

from django.test import SimpleTestCase, TestCase, override_settings

from apps.telegram_bot.handlers.admin.common import AWAITING_CLIENT_LOOKUP, AWAITING_KEY
from apps.telegram_bot.handlers.admin.text_input import admin_text_input


ADMIN_ID = 9001
BOT_TOKEN = '390540012:AA-token-shaped-value'


def _text_update(text, telegram_id=ADMIN_ID):
    from_user = SimpleNamespace(id=telegram_id, username='op')
    return SimpleNamespace(
        callback_query=None, message=SimpleNamespace(text=text, from_user=from_user),
        effective_chat=SimpleNamespace(id=telegram_id), effective_user=from_user,
    )


def _context():
    return SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()), user_data={})


class AdminTextInputDispatchTests(TestCase):
    async def test_a_pending_client_lookup_reaches_the_client_flow(self):
        context = _context()
        context.user_data[AWAITING_KEY] = AWAITING_CLIENT_LOOKUP

        await admin_text_input(_text_update('@nobody'), context)

        context.bot.send_message.assert_awaited_once()
        self.assertIn('не найден', context.bot.send_message.await_args.kwargs['text'])

    async def test_no_pending_state_does_nothing(self):
        context = _context()

        await admin_text_input(_text_update('hello'), context)

        context.bot.send_message.assert_not_awaited()

    async def test_an_unknown_awaiting_value_does_nothing(self):
        context = _context()
        context.user_data[AWAITING_KEY] = 'something_unrecognized'

        await admin_text_input(_text_update('hello'), context)

        context.bot.send_message.assert_not_awaited()


class AdminPendingInputFilterTests(SimpleTestCase):
    """Import lives inside the test: `bot_app` builds the real `Application` at
    import time from `TELEGRAM_BOT_TOKEN`, the same trap `test_feedback.py`
    already works around."""

    def test_the_filter_matches_only_an_admin_with_a_pending_prompt(self):
        with override_settings(TELEGRAM_BOT_TOKEN=BOT_TOKEN, BOT_ADMIN_TELEGRAM_IDS=[ADMIN_ID]):
            bot_app_module = importlib.import_module('apps.telegram_bot.bot_app')
            common = importlib.import_module('apps.telegram_bot.handlers.admin.common')

            other_id = 4242
            bot_app_module.telegram_bot_app.user_data[ADMIN_ID][common.AWAITING_KEY] = AWAITING_CLIENT_LOOKUP

            admin_message = SimpleNamespace(from_user=SimpleNamespace(id=ADMIN_ID))
            admin_no_prompt = SimpleNamespace(from_user=SimpleNamespace(id=99999))
            non_admin_message = SimpleNamespace(from_user=SimpleNamespace(id=other_id))

            self.assertTrue(common.ADMIN_PENDING_INPUT.filter(admin_message))
            self.assertFalse(common.ADMIN_PENDING_INPUT.filter(admin_no_prompt))
            self.assertFalse(common.ADMIN_PENDING_INPUT.filter(non_admin_message))

            bot_app_module.telegram_bot_app.user_data[ADMIN_ID].clear()
