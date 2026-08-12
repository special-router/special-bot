"""Обратная связь бота: что видит пользователь, когда действие не удалось.

Проверяются не экраны, а четыре молчания, доведшие до жалоб: Application без
обработчика ошибок, нажатие без ответа, кнопки сумм при пустом токене
провайдера и кнопка привязки, выключенная флагом раскатки. Каждое из них
проходило прежние тесты насквозь — те звали сборщик экрана, а обработчик до
сборщика не доходил.
"""

import importlib
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from asgiref.sync import sync_to_async
from django.test import SimpleTestCase, TestCase, override_settings
from telegram import CallbackQuery, Chat, Message, Update, User
from telegram.error import BadRequest

from apps.servers.models import TariffServer
from apps.subscriptions.devices import binding_window_open
from apps.telegram_bot.error_handler import FAILURE_TEXT, on_error
from apps.telegram_bot.handlers.add_key import add_key
from apps.telegram_bot.handlers.balance import TOP_UP_UNAVAILABLE, show_balance
from apps.telegram_bot.handlers.faq import faq
from apps.telegram_bot.handlers.main_menu import main_menu
from apps.telegram_bot.handlers.profile import show_profile
from apps.telegram_bot.handlers.referral import referral
from apps.telegram_bot.handlers.remove_key import remove_key, show_keys_for_remove
from apps.telegram_bot.handlers.reset_devices import RESET_DONE_TEXT, reset_devices
from apps.telegram_bot.handlers.show_keys import show_keys
from apps.telegram_bot.handlers.top_up_balance import (
    PROVIDER_FAILED_NOTICE,
    UNAVAILABLE_TOAST,
    top_up_balance_one_month,
)
from apps.telegram_bot.inline_buttons.balance import get_reply_markup_balance
from apps.telegram_bot.inline_buttons.manage_keys import get_reply_markup_manage_keys
from apps.users.models import TelegramUser


# Токен провайдера по форме, но не по содержанию: проверяется только то, что
# он непустой.
PROVIDER_TOKEN = '390540012:TEST:not-a-real-token'
BOT_TOKEN = '390540012:AA-token-shaped-value-for-tests'
CLIENT_ID = 1001

ERROR_LOGGER = 'apps.telegram_bot.error_handler'


def _context():
    return SimpleNamespace(
        bot=SimpleNamespace(send_message=AsyncMock(), send_invoice=AsyncMock(), send_media_group=AsyncMock()),
        error=None,
    )


def _callback_update(data):
    """Апдейт-подделка: обработчикам нужны только эти поля."""
    from_user = SimpleNamespace(id=CLIENT_ID, username='client', is_bot=False)
    query = SimpleNamespace(data=data, from_user=from_user, answer=AsyncMock(), edit_message_text=AsyncMock())
    return SimpleNamespace(
        callback_query=query,
        message=None,
        effective_chat=SimpleNamespace(id=CLIENT_ID),
        effective_user=from_user,
    )


def _real_callback_update(data):
    """Настоящий `Update`: обработчик ошибок проверяет его тип, а не поля."""
    user = User(id=CLIENT_ID, first_name='client', is_bot=False)
    chat = Chat(id=CLIENT_ID, type=Chat.PRIVATE)
    message = Message(message_id=5, date=datetime.now(timezone.utc), chat=chat, from_user=user)
    query = CallbackQuery(id='q-1', from_user=user, chat_instance='instance', data=data, message=message)
    return Update(update_id=77, callback_query=query)


def _screen_text(query):
    return query.edit_message_text.await_args.kwargs['text']


class ErrorHandlerTests(IsolatedAsyncioTestCase):
    """Сбой обязан оставить след в логе и слово пользователю."""

    async def test_a_failure_reaches_the_user_and_carries_context_to_the_log(self):
        update = _real_callback_update('show_balance')
        context = _context()
        context.error = RuntimeError('Payment_provider_invalid')

        with patch(f'{ERROR_LOGGER}.answer_query', new_callable=AsyncMock) as answered:
            with self.assertLogs(ERROR_LOGGER, level=logging.ERROR) as logged:
                await on_error(update, context)

        answered.assert_awaited_once_with(update, FAILURE_TEXT)
        self.assertEqual(context.bot.send_message.await_args.kwargs['text'], FAILURE_TEXT)

        record = logged.records[0]
        self.assertIs(record.exc_info[1], context.error)
        self.assertIn('callback_data=show_balance', record.getMessage())
        self.assertIn(f'telegram_id={CLIENT_ID}', record.getMessage())

    async def test_the_user_never_sees_the_exception_itself(self):
        update = _real_callback_update('show_balance')
        context = _context()
        context.error = RuntimeError('sqlite3.OperationalError: no such table')

        with patch(f'{ERROR_LOGGER}.answer_query', new_callable=AsyncMock):
            with self.assertLogs(ERROR_LOGGER, level=logging.ERROR):
                await on_error(update, context)

        self.assertNotIn('OperationalError', context.bot.send_message.await_args.kwargs['text'])

    async def test_a_failing_notification_does_not_raise_a_second_time(self):
        """Сбой мог быть сетевым — тогда и сообщение о нём не дойдёт."""
        update = _real_callback_update('show_balance')
        context = _context()
        context.error = RuntimeError('boom')
        context.bot.send_message.side_effect = BadRequest('Chat not found')

        with patch(f'{ERROR_LOGGER}.answer_query', new_callable=AsyncMock, side_effect=BadRequest('Query is too old')):
            with self.assertLogs(ERROR_LOGGER, level=logging.ERROR):
                await on_error(update, context)

    async def test_a_failure_outside_an_update_is_logged_and_not_delivered(self):
        context = _context()
        context.error = RuntimeError('polling died')

        with self.assertLogs(ERROR_LOGGER, level=logging.ERROR) as logged:
            await on_error(None, context)

        self.assertIn('update=unknown', logged.records[0].getMessage())
        context.bot.send_message.assert_not_awaited()


class ErrorHandlerRegistrationTests(SimpleTestCase):
    def test_register_handlers_installs_the_error_handler(self):
        """Незарегистрированный обработчик равен его отсутствию: PTB пишет
        «No error handlers are registered» и глотает исключение."""
        # Application собирается на импорте модуля и требует правдоподобный
        # токен, поэтому импорт живёт внутри теста, а не в шапке файла.
        with override_settings(TELEGRAM_BOT_TOKEN=BOT_TOKEN):
            module = importlib.import_module('apps.telegram_bot.register_handlers')
            module.register_handlers()

        self.assertIn(on_error, module.telegram_bot_app.error_handlers)


@override_settings(YOUMONEY_TOKEN='')
class CallbackAnswerTests(TestCase):
    """Нажатие без ответа Telegram крутит «часики» до собственного таймаута."""

    def setUp(self):
        self.context = _context()

    async def test_navigation_handlers_answer_their_query(self):
        for handler, data in (
            (main_menu, 'main_menu'),
            (show_keys, 'show_keys'),
            (show_balance, 'show_balance'),
            (show_profile, 'profile'),
            (referral, 'referral'),
            (show_keys_for_remove, 'show_keys_for_remove'),
            (faq, 'faq'),
        ):
            with self.subTest(callback_data=data):
                update = _callback_update(data)
                await handler(update, self.context)
                update.callback_query.answer.assert_awaited()

    async def test_a_repeated_press_on_add_answers_instead_of_dropping_out(self):
        """Redis гасит повторное списание; молчание делало его неотличимым от отказа."""
        update = _callback_update('add_key:12345678')

        with patch('apps.telegram_bot.handlers.add_key.redis.from_url') as redis_from_url:
            redis_from_url.return_value.get.return_value = b'1'
            await add_key(update, self.context)

        update.callback_query.answer.assert_awaited_once_with(text='Подписка уже добавляется.')

    async def test_removing_a_subscription_that_is_gone_answers(self):
        update = _callback_update('remove_key:4242')

        await remove_key(update, self.context)

        update.callback_query.answer.assert_awaited_once_with(text='Подписка не найдена.')


@override_settings(YOUMONEY_TOKEN='')
class PaymentsWithoutAProviderTests(TestCase):
    """Пустой `YOUMONEY_TOKEN` — то состояние, в котором бот стоял в проде."""

    def setUp(self):
        self.context = _context()

    async def _amount_buttons(self):
        user = await TelegramUser.objects.acreate(telegram_id=CLIENT_ID, username='client')
        keyboard = await get_reply_markup_balance(user)
        pressed = [key.callback_data for row in keyboard.inline_keyboard for key in row if key.callback_data]
        # Промо — начисление, а не платёж: провайдера оно не касается и от
        # токена не зависит.
        return [data for data in pressed if data.startswith('top_up_balance_') and data != 'top_up_balance_promo']

    async def test_amount_buttons_are_absent_without_a_provider(self):
        self.assertEqual(await self._amount_buttons(), [])

    @override_settings(YOUMONEY_TOKEN=PROVIDER_TOKEN)
    async def test_amount_buttons_return_once_a_provider_is_configured(self):
        self.assertEqual(len(await self._amount_buttons()), 5)

    async def test_the_screen_says_why_the_amounts_are_missing(self):
        update = _callback_update('show_balance')

        await show_balance(update, self.context)

        self.assertIn(TOP_UP_UNAVAILABLE, _screen_text(update.callback_query))

    async def test_a_stale_amount_press_explains_itself_and_sends_no_invoice(self):
        update = _callback_update('top_up_balance_one_month')

        await top_up_balance_one_month(update, self.context)

        self.context.bot.send_invoice.assert_not_awaited()
        update.callback_query.answer.assert_awaited_once_with(text=UNAVAILABLE_TOAST)
        self.assertIn(TOP_UP_UNAVAILABLE, _screen_text(update.callback_query))

    @override_settings(YOUMONEY_TOKEN=PROVIDER_TOKEN)
    async def test_a_rejected_invoice_becomes_a_message_instead_of_a_silence(self):
        await TariffServer.objects.acreate(name='Базовый', price=7)
        update = _callback_update('top_up_balance_one_month')
        self.context.bot.send_invoice.side_effect = BadRequest('Payment_provider_invalid')

        with self.assertLogs('apps.telegram_bot.handlers.top_up_balance', level=logging.ERROR):
            await top_up_balance_one_month(update, self.context)

        update.callback_query.answer.assert_awaited_once_with(text=UNAVAILABLE_TOAST)
        self.assertIn(PROVIDER_FAILED_NOTICE, _screen_text(update.callback_query))

    @override_settings(YOUMONEY_TOKEN=PROVIDER_TOKEN)
    async def test_a_working_provider_still_sends_the_invoice_and_answers(self):
        await TariffServer.objects.acreate(name='Базовый', price=7)
        update = _callback_update('top_up_balance_one_month')

        await top_up_balance_one_month(update, self.context)

        self.context.bot.send_invoice.assert_awaited_once()
        update.callback_query.answer.assert_awaited_once_with(text=None)


class DeviceActionTests(TestCase):
    """Привязка идёт сама; пользователю остаётся только освободить места."""

    def setUp(self):
        self.context = _context()

    async def test_the_keyboard_offers_unbinding_and_never_binding(self):
        user = await TelegramUser.objects.acreate(telegram_id=CLIENT_ID, username='client')

        keyboard = await get_reply_markup_manage_keys(user)
        actions = {key.callback_data: key.text for row in keyboard.inline_keyboard for key in row}

        self.assertNotIn('bind_device', actions)
        self.assertIn('Отвязать', actions['reset_devices'])

    async def test_unbinding_opens_the_binding_window(self):
        """Фаза 2 включит окно привязки: без него отвязка стала бы тупиком."""
        update = _callback_update('reset_devices')

        await reset_devices(update, self.context)

        user = await TelegramUser.objects.aget(telegram_id=CLIENT_ID)
        self.assertTrue(await sync_to_async(binding_window_open)(user.id))

    async def test_unbinding_confirms_itself_and_says_what_happens_next(self):
        update = _callback_update('reset_devices')

        await reset_devices(update, self.context)

        update.callback_query.answer.assert_awaited_once_with(text='Устройства отвязаны.')
        self.assertIn(RESET_DONE_TEXT, _screen_text(update.callback_query))

    async def test_the_cooldown_no_longer_points_at_a_button_that_is_gone(self):
        update = _callback_update('reset_devices')
        await reset_devices(update, self.context)

        second = _callback_update('reset_devices')
        await reset_devices(second, self.context)

        text = _screen_text(second.callback_query)
        self.assertNotIn('Привязать устройство', text)
        second.callback_query.answer.assert_awaited_once_with(text='Отвязать пока нельзя.')
