"""Гарантии обращений в поддержку.

Проверяется не разметка экранов, а то, что ломает работу молча: второй открытый
тикет у одного пользователя, черновик, переживший отказ Telegram, ответ
оператора в закрытую тему, потерянное вложение и включённая машинерия при
выключенной настройке. Bot API подменяется целиком, база — настоящая:
единственный открытый тикет держит индекс, а не код, и проверять это на моках
было бы нечего.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from telegram.error import TelegramError

from apps.telegram_bot.handlers.support import (
    MEDIA_KINDS,
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
ADMIN_BASE_URL = 'https://sub.example.test/admin/'
FILE_ID = 'file-42'


def _telegram_user(telegram_id=CLIENT_ID, username='client', is_bot=False, first_name='', last_name=''):
    return SimpleNamespace(
        id=telegram_id, username=username, is_bot=is_bot, first_name=first_name, last_name=last_name
    )


def _operator(telegram_id=5005, username='operator', first_name='Иван', last_name='Петров'):
    return _telegram_user(
        telegram_id=telegram_id, username=username, first_name=first_name, last_name=last_name
    )


def _attachment(kind, file_id=FILE_ID):
    """Так вложение выглядит в `Message`: у фотографии — лестница размеров."""
    if kind == 'photo':
        return [SimpleNamespace(file_id='file-thumbnail'), SimpleNamespace(file_id=file_id)]
    return SimpleNamespace(file_id=file_id)


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


def _message(text, from_user, thread_id, message_id, caption, media):
    message = SimpleNamespace(
        text=text, caption=caption, from_user=from_user, message_thread_id=thread_id, message_id=message_id
    )
    if media is not None:
        setattr(message, media, _attachment(media))
    return message


def _private_update(text=None, from_user=None, *, caption=None, media=None):
    from_user = from_user or _telegram_user()
    return SimpleNamespace(
        callback_query=None,
        message=_message(text, from_user, None, 1, caption, media),
        effective_chat=SimpleNamespace(id=from_user.id),
        effective_user=from_user,
    )


def _topic_update(text, thread_id, from_user=None, *, caption=None, media=None):
    from_user = from_user or _operator()
    return SimpleNamespace(
        callback_query=None,
        message=_message(text, from_user, thread_id, 2, caption, media),
        effective_chat=SimpleNamespace(id=SUPPORT_CHAT),
        effective_user=from_user,
    )


def _context():
    bot = SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=555)),
        create_forum_topic=AsyncMock(return_value=SimpleNamespace(message_thread_id=TOPIC_ID)),
        edit_forum_topic=AsyncMock(),
    )
    for kind in MEDIA_KINDS:
        setattr(bot, f'send_{kind}', AsyncMock())
    return SimpleNamespace(bot=bot)


def _sent_to(bot, chat_id):
    return [call.kwargs for call in bot.send_message.await_args_list if call.kwargs.get('chat_id') == chat_id]


def _buttons(message_kwargs):
    markup = message_kwargs.get('reply_markup')
    return [button for row in markup.inline_keyboard for button in row] if markup is not None else []


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


@override_settings(SUPPORT_CHAT_ID=SUPPORT_CHAT)
class SupportMediaTests(TestCase):
    """Вложения в обе стороны.

    Проверяется не то, что вызван нужный метод Bot API, а то, ради чего вся
    затея: подпись доходит вместе с файлом, файл без подписи всё равно доходит,
    а потерянный файл не уносит с собой текст и не остаётся незамеченным.
    """

    def setUp(self):
        self.context = _context()

    async def _open_and_send(self, *, text=None, caption=None, media=None, from_user=None):
        from_user = from_user or _telegram_user()
        await support_open(_callback_update('support_open', from_user=from_user), self.context)
        await support_message(
            _private_update(text, from_user, caption=caption, media=media), self.context
        )

    async def test_every_attachment_kind_reaches_the_topic_with_its_caption(self):
        for index, (kind, label) in enumerate(MEDIA_KINDS.items()):
            with self.subTest(kind=kind):
                # Свой клиент на каждый вид: у одного пользователя открытое
                # обращение только одно, и вложения легли бы в тот же тикет.
                client = _telegram_user(telegram_id=CLIENT_ID + index, username=f'client{index}')
                await self._open_and_send(caption='вот скриншот', media=kind, from_user=client)

                sender = getattr(self.context.bot, f'send_{kind}')
                self.assertEqual(sender.await_args.kwargs[kind], FILE_ID)
                self.assertEqual(sender.await_args.kwargs['chat_id'], SUPPORT_CHAT)
                self.assertEqual(sender.await_args.kwargs['message_thread_id'], TOPIC_ID)

                posted = _sent_to(self.context.bot, SUPPORT_CHAT)[-1]
                self.assertIn('вот скриншот', posted['text'])
                self.assertIn(label, posted['text'])

    async def test_an_attachment_without_a_caption_still_reaches_the_topic(self):
        await self._open_and_send(media='photo')

        self.context.bot.send_photo.assert_awaited_once()
        posted = _sent_to(self.context.bot, SUPPORT_CHAT)
        self.assertEqual(len(posted), 1)
        self.assertIn('фотография', posted[0]['text'])

        ticket = await SupportTicket.objects.aget()
        self.assertEqual(ticket.subject, '[фотография]')

    async def test_an_unsupported_attachment_is_refused_instead_of_dropped(self):
        """Стикер до оператора не дойдёт, и человек обязан узнать об этом сразу."""
        await self._open_and_send()

        self.assertEqual(await SupportTicket.objects.acount(), 0)
        self.context.bot.create_forum_topic.assert_not_awaited()

        refused = _sent_to(self.context.bot, CLIENT_ID)
        self.assertEqual(len(refused), 1)
        self.assertIn('Вложение не отправлено', refused[0]['text'])

    async def test_a_failed_attachment_keeps_the_text_and_tells_both_sides(self):
        self.context.bot.send_document.side_effect = TelegramError('file is too big')

        await self._open_and_send(caption='логи приложения', media='document')

        posted = _sent_to(self.context.bot, SUPPORT_CHAT)
        self.assertIn('логи приложения', posted[0]['text'])
        self.assertIn('не дошло', posted[-1]['text'])

        delivered = _sent_to(self.context.bot, CLIENT_ID)
        self.assertIn('не дошло', delivered[-1]['text'])

    async def test_every_attachment_kind_reaches_the_client(self):
        await self._open_and_send(text='интернет не работает')

        for kind, label in MEDIA_KINDS.items():
            with self.subTest(kind=kind):
                await support_operator_reply(
                    _topic_update(None, TOPIC_ID, caption='смотрите инструкцию', media=kind), self.context
                )

                sender = getattr(self.context.bot, f'send_{kind}')
                self.assertEqual(sender.await_args.kwargs[kind], FILE_ID)
                self.assertEqual(sender.await_args.kwargs['chat_id'], CLIENT_ID)
                self.assertIsNone(sender.await_args.kwargs['message_thread_id'])

                delivered = _sent_to(self.context.bot, CLIENT_ID)[-1]
                self.assertIn('смотрите инструкцию', delivered['text'])
                self.assertIn(label, delivered['text'])

    async def test_an_attachment_lost_on_the_way_to_the_client_is_reported_in_the_topic(self):
        await self._open_and_send(text='интернет не работает')
        self.context.bot.send_video.side_effect = TelegramError('bot was blocked by the user')
        self.context.bot.send_message.reset_mock()

        await support_operator_reply(_topic_update('смотрите видео', TOPIC_ID, media='video'), self.context)

        self.assertIn('смотрите видео', _sent_to(self.context.bot, CLIENT_ID)[0]['text'])
        self.assertIn('не дошло', _sent_to(self.context.bot, SUPPORT_CHAT)[-1]['text'])

    async def test_an_unsupported_operator_attachment_is_refused_in_the_topic(self):
        await self._open_and_send(text='интернет не работает')
        self.context.bot.send_message.reset_mock()

        await support_operator_reply(_topic_update(None, TOPIC_ID), self.context)

        self.assertEqual(_sent_to(self.context.bot, CLIENT_ID), [])
        self.assertIn('не уйдёт', _sent_to(self.context.bot, SUPPORT_CHAT)[-1]['text'])

    async def test_an_attachment_in_a_closed_topic_never_reaches_the_client(self):
        await self._open_and_send(text='интернет не работает')
        ticket = await SupportTicket.objects.aget()
        await support_close(_callback_update(f'support_close:{ticket.id}'), self.context)
        self.context.bot.send_message.reset_mock()

        await support_operator_reply(
            _topic_update(None, TOPIC_ID, caption='инструкция', media='photo'), self.context
        )

        self.assertEqual(_sent_to(self.context.bot, CLIENT_ID), [])
        self.context.bot.send_photo.assert_not_awaited()


@override_settings(SUPPORT_CHAT_ID=SUPPORT_CHAT, ADMIN_BASE_URL=ADMIN_BASE_URL)
class SupportOperatorTests(TestCase):
    """Кто отвечает клиенту и что с этим видно в теме."""

    def setUp(self):
        self.context = _context()

    async def _ticket(self):
        await support_open(_callback_update('support_open'), self.context)
        await support_message(_private_update('интернет не работает'), self.context)
        return await SupportTicket.objects.aget()

    async def test_the_first_operator_to_reply_takes_the_ticket(self):
        ticket = await self._ticket()

        await support_operator_reply(_topic_update('смотрю', TOPIC_ID), self.context)

        ticket = await SupportTicket.objects.aget()
        self.assertEqual(ticket.operator_telegram_id, 5005)
        self.assertEqual(ticket.operator_name, 'Иван Петров')

        renamed = self.context.bot.edit_forum_topic.await_args.kwargs
        self.assertEqual(renamed['name'], f'✅ Ticket #{ticket.id} | @client · Иван Петров')

    async def test_the_client_sees_the_name_of_the_operator_who_answered(self):
        await self._ticket()
        self.context.bot.send_message.reset_mock()

        await support_operator_reply(_topic_update('перезагрузите роутер', TOPIC_ID), self.context)

        delivered = _sent_to(self.context.bot, CLIENT_ID)[-1]
        self.assertIn('Иван Петров', delivered['text'])
        self.assertNotIn('5005', delivered['text'])

    async def test_a_second_operator_answers_without_taking_the_ticket_over(self):
        """Обращение остаётся за первым, но клиент видит того, кто пишет."""
        ticket = await self._ticket()
        await support_operator_reply(_topic_update('смотрю', TOPIC_ID), self.context)
        self.context.bot.edit_forum_topic.reset_mock()

        colleague = _operator(telegram_id=6006, username='maria_op', first_name='Мария', last_name='Сидорова')
        await support_operator_reply(
            _topic_update('перезагрузите роутер', TOPIC_ID, from_user=colleague), self.context
        )

        ticket = await SupportTicket.objects.aget()
        self.assertEqual(ticket.operator_telegram_id, 5005)
        self.assertEqual(ticket.operator_name, 'Иван Петров')
        self.context.bot.edit_forum_topic.assert_not_awaited()

        delivered = _sent_to(self.context.bot, CLIENT_ID)[-1]
        self.assertIn('Мария Сидорова', delivered['text'])

    async def test_an_operator_without_a_name_is_never_signed_with_an_account_id(self):
        await self._ticket()
        self.context.bot.send_message.reset_mock()

        anonymous = _operator(telegram_id=7007, username=None, first_name='', last_name='')
        await support_operator_reply(_topic_update('ответ', TOPIC_ID, from_user=anonymous), self.context)

        delivered = _sent_to(self.context.bot, CLIENT_ID)[-1]
        self.assertIn('Оператор', delivered['text'])
        self.assertNotIn('7007', delivered['text'])

    async def test_a_closed_topic_keeps_the_operator_in_its_name(self):
        ticket = await self._ticket()
        await support_operator_reply(_topic_update('смотрю', TOPIC_ID), self.context)

        await support_close(_callback_update(f'support_close:{ticket.id}'), self.context)

        renamed = self.context.bot.edit_forum_topic.await_args.kwargs
        self.assertEqual(renamed['name'], f'❌ Ticket #{ticket.id} | @client · Иван Петров')

    async def test_the_topic_links_to_that_customer_in_the_admin(self):
        await self._ticket()
        user = await TelegramUser.objects.aget(telegram_id=CLIENT_ID)

        posted = _sent_to(self.context.bot, SUPPORT_CHAT)[0]
        links = [button for button in _buttons(posted) if button.url]

        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].url, f'{ADMIN_BASE_URL}users/telegramuser/{user.id}/change/')

    @override_settings(ADMIN_BASE_URL='')
    async def test_without_an_admin_url_the_topic_keeps_the_close_button(self):
        """Битая ссылка отправила бы Bot API отклонить всю клавиатуру целиком."""
        ticket = await self._ticket()

        posted = _sent_to(self.context.bot, SUPPORT_CHAT)[0]
        buttons = _buttons(posted)

        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0].callback_data, f'support_close:{ticket.id}')


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
