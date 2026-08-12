"""Гарантии обращений в поддержку.

Проверяется не разметка экранов, а то, что ломает работу молча: второй открытый
тикет у одного пользователя, черновик, переживший отказ Telegram, ответ
оператора в закрытую тему и включённая машинерия при выключенной настройке.
Bot API подменяется целиком, база — настоящая: единственный открытый тикет
держит индекс, а не код, и проверять это на моках было бы нечего.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from telegram.error import TelegramError

from apps.telegram_bot.handlers.support import (
    support_close,
    support_message,
    support_open,
    support_operator_reply,
)
from apps.telegram_bot.inline_buttons.start import SUPPORT_URL, get_reply_markup_main_menu
from apps.telegram_bot.models import SupportPrompt, SupportTicket
from apps.telegram_bot.support import open_ticket
from apps.users.models import TelegramUser


SUPPORT_CHAT = -1001234567890
TOPIC_ID = 42
CLIENT_ID = 1001


def _telegram_user(telegram_id=CLIENT_ID, username='client', is_bot=False):
    return SimpleNamespace(id=telegram_id, username=username, is_bot=is_bot)


def _callback_update(data, from_user=None):
    from_user = from_user or _telegram_user()
    query = SimpleNamespace(
        data=data, from_user=from_user, answer=AsyncMock(), edit_message_text=AsyncMock()
    )
    return SimpleNamespace(
        callback_query=query,
        message=None,
        effective_chat=SimpleNamespace(id=from_user.id),
        effective_user=from_user,
    )


def _private_update(text, from_user=None):
    from_user = from_user or _telegram_user()
    message = SimpleNamespace(text=text, from_user=from_user, message_thread_id=None, message_id=1)
    return SimpleNamespace(
        callback_query=None,
        message=message,
        effective_chat=SimpleNamespace(id=from_user.id),
        effective_user=from_user,
    )


def _topic_update(text, thread_id, from_user=None):
    from_user = from_user or _telegram_user(telegram_id=5005, username='operator')
    message = SimpleNamespace(text=text, from_user=from_user, message_thread_id=thread_id, message_id=2)
    return SimpleNamespace(
        callback_query=None,
        message=message,
        effective_chat=SimpleNamespace(id=SUPPORT_CHAT),
        effective_user=from_user,
    )


def _context():
    bot = SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=555)),
        create_forum_topic=AsyncMock(return_value=SimpleNamespace(message_thread_id=TOPIC_ID)),
        edit_forum_topic=AsyncMock(),
    )
    return SimpleNamespace(bot=bot)


def _sent_to(bot, chat_id):
    return [call.kwargs for call in bot.send_message.await_args_list if call.kwargs.get('chat_id') == chat_id]


@override_settings(SUPPORT_CHAT_ID=SUPPORT_CHAT)
class SupportTicketStorageTests(TestCase):
    """Правила хранения — то, что должно держаться без участия обработчиков."""

    def setUp(self):
        self.user = TelegramUser.objects.create(telegram_id=CLIENT_ID, username='client')

    def test_second_open_ticket_is_refused_by_the_database(self):
        SupportTicket.objects.create(user=self.user, telegram_username='@client')

        with self.assertRaises(IntegrityError), transaction.atomic():
            SupportTicket.objects.create(user=self.user, telegram_username='@client')

    def test_a_closed_ticket_frees_the_slot(self):
        first = SupportTicket.objects.create(user=self.user, telegram_username='@client')
        first.status = SupportTicket.STATUS_CLOSED
        first.save(update_fields=['status'])

        SupportTicket.objects.create(user=self.user, telegram_username='@client')

        self.assertEqual(SupportTicket.objects.filter(status=SupportTicket.STATUS_OPEN).count(), 1)

    def test_open_ticket_adopts_the_row_that_won_a_concurrent_create(self):
        """Проверка и вставка — два запроса; проигравший обязан подобрать чужую строку.

        Гонка воспроизводится буквально: предварительная проверка не видит
        ничего, а вставка упирается в настоящий частичный индекс.
        """
        winner = SupportTicket.objects.create(user=self.user, telegram_username='@winner')

        with patch('django.db.models.query.QuerySet.first', return_value=None):
            ticket, created = open_ticket(self.user.id, '@loser', 'текст обращения')

        self.assertFalse(created)
        self.assertEqual(ticket.pk, winner.pk)
        self.assertEqual(SupportTicket.objects.count(), 1)

    def test_stored_subject_is_bounded(self):
        ticket, _created = open_ticket(self.user.id, '@client', 'а' * 5000)

        self.assertLessEqual(len(ticket.subject), SupportTicket.SUBJECT_MAX_LENGTH)


@override_settings(SUPPORT_CHAT_ID=SUPPORT_CHAT)
class SupportFlowTests(TestCase):
    def setUp(self):
        self.context = _context()

    async def _open_and_send(self, text, context=None):
        context = context or self.context
        await support_open(_callback_update('support_open'), context)
        await support_message(_private_update(text), context)

    async def test_a_message_after_the_button_opens_a_ticket_in_a_new_topic(self):
        await self._open_and_send('интернет не работает')

        ticket = await SupportTicket.objects.aget()
        self.assertEqual(ticket.topic_id, TOPIC_ID)
        self.assertEqual(ticket.status, SupportTicket.STATUS_OPEN)

        self.context.bot.create_forum_topic.assert_awaited_once()
        name = self.context.bot.create_forum_topic.await_args.kwargs['name']
        self.assertEqual(name, f'✅ Ticket #{ticket.id} | @client')

        posted = _sent_to(self.context.bot, SUPPORT_CHAT)
        self.assertEqual(len(posted), 1)
        self.assertIn('интернет не работает', posted[0]['text'])
        self.assertEqual(posted[0]['message_thread_id'], TOPIC_ID)

    async def test_the_prompt_is_consumed_by_exactly_one_message(self):
        """Один такт на нажатие — это и есть вся защита от потока сообщений."""
        await self._open_and_send('первое сообщение')
        await support_message(_private_update('второе сообщение'), self.context)

        posted = _sent_to(self.context.bot, SUPPORT_CHAT)
        self.assertEqual(len(posted), 1)
        self.assertIn('первое сообщение', posted[0]['text'])
        self.assertFalse(await SupportPrompt.objects.aexists())

    async def test_a_second_button_press_writes_into_the_same_ticket(self):
        await self._open_and_send('первое сообщение')
        await self._open_and_send('второе сообщение')

        self.assertEqual(await SupportTicket.objects.acount(), 1)
        self.context.bot.create_forum_topic.assert_awaited_once()
        self.assertEqual(len(_sent_to(self.context.bot, SUPPORT_CHAT)), 2)

    async def test_a_refused_topic_leaves_no_ticket_and_the_user_can_try_again(self):
        """Черновик без темы занял бы единственный слот и не закрывался бы."""
        self.context.bot.create_forum_topic.side_effect = TelegramError('not enough rights')

        await self._open_and_send('интернет не работает')

        self.assertEqual(await SupportTicket.objects.acount(), 0)
        self.assertEqual(_sent_to(self.context.bot, SUPPORT_CHAT), [])

        self.context.bot.create_forum_topic.side_effect = None
        await self._open_and_send('интернет не работает')

        self.assertEqual(await SupportTicket.objects.acount(), 1)

    async def test_an_operator_reply_reaches_the_client(self):
        await self._open_and_send('интернет не работает')
        self.context.bot.send_message.reset_mock()

        await support_operator_reply(_topic_update('перезагрузите приложение', TOPIC_ID), self.context)

        delivered = _sent_to(self.context.bot, CLIENT_ID)
        self.assertEqual(len(delivered), 1)
        self.assertIn('перезагрузите приложение', delivered[0]['text'])

    async def test_a_reply_into_a_closed_topic_never_reaches_the_client(self):
        await self._open_and_send('интернет не работает')
        ticket = await SupportTicket.objects.aget()
        await support_close(_callback_update(f'support_close:{ticket.id}'), self.context)
        self.context.bot.send_message.reset_mock()

        await support_operator_reply(_topic_update('запоздалый ответ', TOPIC_ID), self.context)

        self.assertEqual(_sent_to(self.context.bot, CLIENT_ID), [])

    async def test_the_bots_own_posts_in_the_topic_are_not_relayed(self):
        await self._open_and_send('интернет не работает')
        self.context.bot.send_message.reset_mock()

        author = _telegram_user(telegram_id=7007, username='special_bot', is_bot=True)
        await support_operator_reply(_topic_update('Обращение № 1', TOPIC_ID, from_user=author), self.context)

        self.assertEqual(_sent_to(self.context.bot, CLIENT_ID), [])

    async def test_closing_renames_the_topic_and_tells_the_client(self):
        await self._open_and_send('интернет не работает')
        ticket = await SupportTicket.objects.aget()
        self.context.bot.send_message.reset_mock()

        await support_close(_callback_update(f'support_close:{ticket.id}'), self.context)

        self.context.bot.edit_forum_topic.assert_awaited_once()
        renamed = self.context.bot.edit_forum_topic.await_args.kwargs
        self.assertEqual(renamed['name'], f'❌ Ticket #{ticket.id} | @client')
        self.assertEqual(renamed['message_thread_id'], TOPIC_ID)

        ticket = await SupportTicket.objects.aget()
        self.assertEqual(ticket.status, SupportTicket.STATUS_CLOSED)
        self.assertIsNotNone(ticket.closed_at)
        self.assertEqual(ticket.meta['closed_by'], '@client')

        delivered = _sent_to(self.context.bot, CLIENT_ID)
        self.assertEqual(len(delivered), 1)
        self.assertIn('закрыто', delivered[0]['text'].lower())

    async def test_closing_frees_the_slot_for_a_new_ticket(self):
        await self._open_and_send('первое обращение')
        first = await SupportTicket.objects.aget()
        await support_close(_callback_update(f'support_close:{first.id}'), self.context)

        await self._open_and_send('второе обращение')

        self.assertEqual(await SupportTicket.objects.filter(status=SupportTicket.STATUS_OPEN).acount(), 1)
        self.assertEqual(await SupportTicket.objects.acount(), 2)

    async def test_a_message_without_the_button_is_ignored(self):
        await support_message(_private_update('просто написал в бота'), self.context)

        self.assertEqual(await SupportTicket.objects.acount(), 0)
        self.context.bot.create_forum_topic.assert_not_awaited()


class SupportDisabledTests(TestCase):
    """Пока чата операторов нет, бот обязан вести себя ровно как раньше."""

    @override_settings(SUPPORT_CHAT_ID=0)
    async def test_the_menu_keeps_the_plain_support_link(self):
        keyboard = await get_reply_markup_main_menu()

        support = [
            button
            for row in keyboard.inline_keyboard
            for button in row
            if button.text.endswith('Поддержка')
        ]
        self.assertEqual(len(support), 1)
        self.assertEqual(support[0].url, SUPPORT_URL)
        self.assertIsNone(support[0].callback_data)

    @override_settings(SUPPORT_CHAT_ID=SUPPORT_CHAT)
    async def test_the_menu_switches_to_the_in_bot_flow_when_the_chat_is_set(self):
        keyboard = await get_reply_markup_main_menu()

        support = [
            button
            for row in keyboard.inline_keyboard
            for button in row
            if button.text.endswith('Поддержка')
        ]
        self.assertEqual(support[0].callback_data, 'support_open')
        self.assertIsNone(support[0].url)

    @override_settings(SUPPORT_CHAT_ID=0)
    async def test_no_ticket_is_created_while_the_feature_is_off(self):
        """Обработчики при выключенной настройке не регистрируются вовсе.

        Здесь они вызываются напрямую, в обход регистрации: даже подключённые
        по ошибке, они не должны ни завести тикет, ни начать ждать сообщение.
        """
        context = _context()
        await support_open(_callback_update('support_open'), context)
        await support_message(_private_update('интернет не работает'), context)

        self.assertEqual(await SupportTicket.objects.acount(), 0)
        self.assertFalse(await SupportPrompt.objects.aexists())
        context.bot.create_forum_topic.assert_not_awaited()
