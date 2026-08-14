"""The admin identity gate: who may reach the in-bot admin panel, and how a
non-admin is refused.

A non-admin must be indistinguishable from someone poking an unrecognized
command — not an error, not a toast, nothing — the same reasoning
`apps.subscriptions.views._refused()` uses for the subscription endpoint.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

from django.test import TestCase, override_settings

from apps.telegram_bot.admin_auth import admin_only, is_bot_admin
from apps.telegram_bot.handlers.admin.menu import admin_command, admin_menu


ADMIN_ID = 9001
OTHER_ID = 9002


def _callback_update(data, telegram_id):
    query = SimpleNamespace(
        data=data, from_user=SimpleNamespace(id=telegram_id, username='u'),
        answer=AsyncMock(), edit_message_text=AsyncMock(),
    )
    return SimpleNamespace(
        callback_query=query, message=None,
        effective_chat=SimpleNamespace(id=telegram_id), effective_user=query.from_user,
    )


def _command_update(telegram_id):
    from_user = SimpleNamespace(id=telegram_id, username='u')
    return SimpleNamespace(
        callback_query=None, message=SimpleNamespace(text='/admin', from_user=from_user),
        effective_chat=SimpleNamespace(id=telegram_id), effective_user=from_user,
    )


def _context():
    bot = SimpleNamespace(send_message=AsyncMock())
    return SimpleNamespace(bot=bot, user_data={})


@override_settings(BOT_ADMIN_TELEGRAM_IDS=[ADMIN_ID])
class IsBotAdminTests(TestCase):
    def test_the_listed_id_is_admin(self):
        self.assertTrue(is_bot_admin(ADMIN_ID))

    def test_an_unlisted_id_is_not_admin(self):
        self.assertFalse(is_bot_admin(OTHER_ID))

    @override_settings(BOT_ADMIN_TELEGRAM_IDS=[])
    def test_an_empty_list_means_nobody_is_admin(self):
        self.assertFalse(is_bot_admin(ADMIN_ID))

    @override_settings(BOT_ADMIN_TELEGRAM_IDS='not-a-list')
    def test_a_malformed_setting_means_nobody_is_admin(self):
        self.assertFalse(is_bot_admin(ADMIN_ID))


class AdminOnlyDecoratorTests(TestCase):
    async def test_a_non_admin_gets_no_response_at_all(self):
        called = AsyncMock()

        @admin_only
        async def handler(update, context):
            await called()

        with override_settings(BOT_ADMIN_TELEGRAM_IDS=[ADMIN_ID]):
            await handler(_callback_update('admin_menu', OTHER_ID), _context())

        called.assert_not_awaited()

    async def test_an_admin_reaches_the_handler(self):
        called = AsyncMock()

        @admin_only
        async def handler(update, context):
            await called()

        with override_settings(BOT_ADMIN_TELEGRAM_IDS=[ADMIN_ID]):
            await handler(_callback_update('admin_menu', ADMIN_ID), _context())

        called.assert_awaited_once()


@override_settings(BOT_ADMIN_TELEGRAM_IDS=[ADMIN_ID])
class AdminEntryPointTests(TestCase):
    async def test_admin_command_from_a_non_admin_sends_nothing(self):
        context = _context()

        await admin_command(_command_update(OTHER_ID), context)

        context.bot.send_message.assert_not_awaited()

    async def test_admin_command_from_an_admin_shows_the_menu(self):
        context = _context()

        await admin_command(_command_update(ADMIN_ID), context)

        context.bot.send_message.assert_awaited_once()
        self.assertIn('Админ-панель', context.bot.send_message.await_args.kwargs['text'])

    async def test_a_forged_admin_callback_from_a_non_admin_gets_no_response(self):
        update = _callback_update('admin_menu', OTHER_ID)

        await admin_menu(update, _context())

        update.callback_query.answer.assert_not_awaited()
        update.callback_query.edit_message_text.assert_not_awaited()

    async def test_the_admin_menu_callback_edits_the_message_for_an_admin(self):
        update = _callback_update('admin_menu', ADMIN_ID)

        await admin_menu(update, _context())

        update.callback_query.edit_message_text.assert_awaited_once()
