"""Broadcasts from the in-bot admin panel: the multi-step flow, and its confirm gate.

`test_broadcast_ops.py` already proves the shared state machine; this file
proves the bot's own multi-step prompt (audience -> title -> message ->
confirm -> send) drives it correctly, and that a tampered confirmation is
refused here exactly as it is from Django admin.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from apps.telegram_bot.handlers.admin.broadcast import (
    BOT_SERVICE_ACCOUNT_USERNAME,
    admin_broadcast,
    admin_broadcast_audience,
    admin_broadcast_cancel,
    admin_broadcast_send,
    handle_message_text,
    handle_title_text,
)
from apps.telegram_bot.models import Broadcast
from apps.users.models import TelegramUser


ADMIN_ID = 9001
TITLE = 'Плановое уведомление'
MESSAGE = 'Текст рассылки достаточной длины для прохождения проверки.'


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
class BroadcastFlowTests(TestCase):
    def setUp(self):
        TelegramUser.objects.create(telegram_id=1, username='one')
        TelegramUser.objects.create(telegram_id=2, username='two')

    async def _to_confirm_screen(self, context):
        await admin_broadcast(_callback_update('admin_broadcast'), context)
        await admin_broadcast_audience(_callback_update('admin_broadcast_audience:all'), context)
        await handle_title_text(_text_update(TITLE), context)
        await handle_message_text(_text_update(MESSAGE), context)

    async def test_the_full_flow_queues_exactly_one_broadcast(self):
        context = _context()

        with patch('apps.telegram_bot.tasks.safe_broadcast_v1.delay'):
            await self._to_confirm_screen(context)
            await admin_broadcast_send(_callback_update('admin_broadcast_send'), context)

        self.assertEqual(await Broadcast.objects.acount(), 1)
        broadcast = await Broadcast.objects.select_related('created_by').aget()
        self.assertEqual(broadcast.status, 'queued')
        self.assertEqual(broadcast.total_users, 2)
        self.assertEqual(broadcast.title, TITLE)
        self.assertEqual(broadcast.created_by.username, BOT_SERVICE_ACCOUNT_USERNAME)
        self.assertFalse(broadcast.created_by.is_active)

    async def test_the_confirm_screen_shows_audience_count_and_message(self):
        context = _context()

        await self._to_confirm_screen(context)

        # handle_message_text renders the confirm screen as a new message.
        text = context.bot.send_message.await_args.kwargs['text']
        self.assertIn('Получателей: 2', text)
        self.assertIn(TITLE, text)
        self.assertIn(MESSAGE, text)

    async def test_cancel_returns_the_broadcast_to_draft(self):
        context = _context()
        await self._to_confirm_screen(context)

        await admin_broadcast_cancel(_callback_update('admin_broadcast_cancel'), context)

        broadcast = await Broadcast.objects.aget()
        self.assertEqual(broadcast.status, 'draft')

    async def test_a_tampered_digest_is_refused_exactly_like_django_admin(self):
        context = _context()
        await self._to_confirm_screen(context)
        context.user_data['admin_broadcast_digest'] = 'not-the-real-digest'
        update = _callback_update('admin_broadcast_send')

        await admin_broadcast_send(update, context)

        broadcast = await Broadcast.objects.aget()
        self.assertEqual(broadcast.status, 'confirming')
        text = update.callback_query.edit_message_text.await_args.kwargs['text']
        self.assertIn('изменились', text)

    async def test_sending_without_a_prior_confirm_screen_is_refused(self):
        update = _callback_update('admin_broadcast_send')

        await admin_broadcast_send(update, _context())

        update.callback_query.answer.assert_awaited_once()
        self.assertFalse(await Broadcast.objects.aexists())

    async def test_an_empty_title_is_rejected_and_stays_on_the_title_prompt(self):
        context = _context()
        await admin_broadcast(_callback_update('admin_broadcast'), context)
        await admin_broadcast_audience(_callback_update('admin_broadcast_audience:all'), context)

        await handle_title_text(_text_update('   '), context)

        self.assertEqual(context.user_data.get('admin_awaiting'), 'broadcast_title')

    async def test_a_too_short_message_is_rejected_and_stays_on_the_message_prompt(self):
        context = _context()
        await admin_broadcast(_callback_update('admin_broadcast'), context)
        await admin_broadcast_audience(_callback_update('admin_broadcast_audience:all'), context)
        await handle_title_text(_text_update(TITLE), context)

        await handle_message_text(_text_update('коротко'), context)

        self.assertEqual(context.user_data.get('admin_awaiting'), 'broadcast_message')
        self.assertFalse(await Broadcast.objects.aexists())

    async def test_an_unknown_audience_is_refused(self):
        update = _callback_update('admin_broadcast_audience:not-real')

        await admin_broadcast_audience(update, _context())

        update.callback_query.answer.assert_awaited_once()

    async def test_a_non_admin_gets_no_response_from_admin_broadcast(self):
        update = _callback_update('admin_broadcast', telegram_id=4242)

        await admin_broadcast(update, _context())

        update.callback_query.edit_message_text.assert_not_awaited()


class BotServiceAccountReuseTests(TestCase):
    async def test_the_service_account_is_reused_across_broadcasts(self):
        from apps.telegram_bot.handlers.admin.broadcast import _bot_service_account

        first = await _bot_service_account()
        second = await _bot_service_account()

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(await User.objects.filter(username=BOT_SERVICE_ACCOUNT_USERNAME).acount(), 1)
