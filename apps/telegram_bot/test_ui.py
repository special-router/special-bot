"""Гарантии, которые обязан держать интерфейс бота.

Экраны собираются здесь без базы: сборщики — обычные функции, а всё, что ходит
в ORM, подменяется. Проверяется не вёрстка, а то, что ломает пользователя
молча: пропавшая иконка, custom_emoji в теле, съехавший `callback_data` и
редактирование якорного сообщения.
"""

import re
from contextlib import ExitStack
from decimal import Decimal
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from django.test import override_settings
from telegram.error import BadRequest

from apps.analytics.balance_split import BalanceSplit
from apps.telegram_bot import icons
from apps.telegram_bot.handlers.balance import build_balance_screen
from apps.telegram_bot.handlers.faq import build_faq_screen
from apps.telegram_bot.handlers.main_menu import build_main_menu_screen
from apps.telegram_bot.handlers.profile import build_profile_screen
from apps.telegram_bot.handlers.referral import build_referral_screen
from apps.telegram_bot.handlers.devices import build_devices_screen
from apps.telegram_bot.handlers.show_keys import build_keys_screen
from apps.telegram_bot.handlers.subscription import build_subscription_screen
from apps.telegram_bot.ui import render_screen


# Набор `callback_data`, на который завязана диспетчеризация в
# register_handlers. Переменная часть (nonce у add_key, id устройства у
# unbind_device) нормализуется — сверяется имя, а не значение.
EXPECTED_CALLBACK_DATA = frozenset(
    {
        'add_key:*',
        'faq',
        'main_menu',
        'profile',
        'referral',
        'add_device_slot',
        'drop_device_slot',
        'show_balance',
        'show_keys',
        'show_devices',
        'unbind_device:*',
        'top_up_balance_one_month',
        'top_up_balance_promo',
        'top_up_balance_six_month',
        'top_up_balance_three_month',
        'top_up_balance_two_month',
        'top_up_balance_year',
    }
)


class AsyncItems:
    def __init__(self, items):
        self._items = items

    def __aiter__(self):
        async def iterate():
            for item in self._items:
                yield item

        return iterate()


def _normalize(callback_data: str) -> str:
    return re.sub(r':\d+$', ':*', callback_data)


class ScreenGuaranteesTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = SimpleNamespace(
            id=10,
            telegram_id=1001,
            balance=100,
            created_at=SimpleNamespace(strftime=lambda _fmt: '01.01.2026'),
        )
        # Разложение баланса ходит в журнал транзакций; здесь оно задаётся, а не
        # считается — проверяется, что экран из него делает, а не арифметика.
        self.split = BalanceSplit(real=Decimal('100.00'), bonus=Decimal('0.00'))
        self.connection = SimpleNamespace(
            id=7,
            device_limit=3,
            server=SimpleNamespace(name='SPECIAL', tariff=SimpleNamespace(price=Decimal('7.00'))),
            enabled=True,
            created_at=SimpleNamespace(strftime=lambda _fmt: '01.01.2026'),
            devices=SimpleNamespace(
                order_by=lambda *_fields: AsyncItems([
                    SimpleNamespace(device_model='iPhone 15 Pro', device_os='iOS'),
                ]),
            ),
        )

    async def build_every_screen(self) -> dict[str, tuple[str, object]]:
        """Собрать все экраны бота за один проход, подменив всё, что ходит в ORM.

        Подменяется имя модели в каждом модуле, а не `Model.objects`: класс у
        всех обработчиков один, и патч атрибута класса перетирался бы соседним.
        """
        connections = [self.connection]

        with ExitStack() as stack:

            def patched(target, **kwargs):
                return stack.enter_context(patch(target, **kwargs))

            # Кнопки сумм есть только при настроенном провайдере; их отсутствие
            # без него — предмет отдельного теста в test_feedback.py.
            stack.enter_context(override_settings(YOUMONEY_TOKEN='390540012:TEST:token'))

            patched('apps.telegram_bot.utils.split_balance', return_value=self.split)

            for module in ('main_menu', 'profile'):
                objects = patched(f'apps.telegram_bot.handlers.{module}.UserVPN').objects
                objects.filter_by_user.return_value.filter_by_enabled.return_value.acount = AsyncMock(return_value=1)

            telegram_users = patched('apps.telegram_bot.handlers.profile.TelegramUser').objects
            telegram_users.annotate_balance.return_value.aget = AsyncMock(return_value=self.user)

            keys_objects = patched('apps.telegram_bot.handlers.show_keys.UserVPN').objects
            keys_objects.with_related_server.return_value.filter.return_value = AsyncItems(connections)
            patched(
                'apps.telegram_bot.handlers.show_keys.get_user_access_url',
                new_callable=AsyncMock,
                return_value='https://sub.example.test/sub/stable',
            )

            device_objects = patched('apps.telegram_bot.handlers.devices.UserVPN').objects
            device_objects.select_related.return_value.filter.return_value.order_by.return_value.afirst = \
                AsyncMock(return_value=self.connection)
            patched('apps.telegram_bot.handlers.devices.bound_devices', return_value=[
                SimpleNamespace(id=31, device_model='iPhone 15 Pro', device_os='iOS'),
            ])

            tariffs = patched('apps.telegram_bot.handlers.balance.TariffServer').objects
            tariffs.afirst = AsyncMock(return_value=SimpleNamespace(price=7))
            transactions = patched('apps.telegram_bot.inline_buttons.balance.Transaction').objects
            transactions.filter_by_user.return_value.filter_by_source.return_value.aexists = AsyncMock(
                return_value=False
            )

            subscription_objects = patched('apps.telegram_bot.handlers.subscription.UserVPN').objects
            queryset = subscription_objects.with_related_server.return_value.filter.return_value
            queryset.order_by.return_value.afirst = AsyncMock(return_value=self.connection)
            patched(
                'apps.telegram_bot.handlers.subscription.get_subscription_url',
                new_callable=AsyncMock,
                return_value='https://sub.example.test/sub/anchor',
            )

            return {
                'main_menu': await build_main_menu_screen(self.user),
                'start': await build_main_menu_screen(self.user, greeting=True),
                'profile': await build_profile_screen(self.user),
                'keys': await build_keys_screen(self.user),
                'keys_notice': await build_keys_screen(self.user, notice='Подписка подключена.'),
                'keys_disconnected': await self._disconnected_keys_screen(),
                'devices': await build_devices_screen(self.user),
                'balance': await build_balance_screen(self.user),
                'referral': await build_referral_screen(self.user),
                'faq': await build_faq_screen(),
                'subscription': await build_subscription_screen(self.user),
            }

    async def _disconnected_keys_screen(self):
        """Кнопка «Подключить» есть только пока подписка не работает."""
        self.connection.enabled = False
        try:
            return await build_keys_screen(self.user)
        finally:
            self.connection.enabled = True

    @override_settings(TELEGRAM_BUTTON_ICONS_ENABLED=False)
    async def test_every_screen_builds_with_icons_disabled(self):
        """Выключенные иконки — рабочий режим по умолчанию, а не аварийный."""
        screens = await self.build_every_screen()

        for name, (text, keyboard) in screens.items():
            with self.subTest(screen=name):
                self.assertTrue(text.strip(), 'экран собрался пустым')
                self.assertTrue(keyboard.inline_keyboard, 'экран остался без клавиатуры')

    async def test_icons_never_change_screen_text(self):
        """Иконки живут только в кнопках: текст экрана от флага не зависит."""
        with override_settings(TELEGRAM_BUTTON_ICONS_ENABLED=False):
            without_icons = {name: text for name, (text, _) in (await self.build_every_screen()).items()}
        with override_settings(TELEGRAM_BUTTON_ICONS_ENABLED=True):
            with_icons = {name: text for name, (text, _) in (await self.build_every_screen()).items()}

        self.assertEqual(without_icons, with_icons)

    @override_settings(TELEGRAM_BUTTON_ICONS_ENABLED=False)
    async def test_disabled_icons_leave_a_fallback_emoji_in_the_button(self):
        """Кнопка без иконки обязана остаться понятной сама по себе."""
        _text, keyboard = (await self.build_every_screen())['main_menu']

        labels = [button.text for row in keyboard.inline_keyboard for button in row]
        for label in labels:
            with self.subTest(label=label):
                self.assertTrue(icons.is_emoji(label[0]), 'в подписи нет запасного эмодзи')

    @override_settings(TELEGRAM_BUTTON_ICONS_ENABLED=True)
    async def test_enabled_icons_move_the_emoji_out_of_the_button_text(self):
        _text, keyboard = (await self.build_every_screen())['main_menu']

        for row in keyboard.inline_keyboard:
            for button in row:
                with self.subTest(label=button.text):
                    self.assertFalse(icons.is_emoji(button.text[0]), 'эмодзи осталось в подписи вместе с иконкой')

    @override_settings(TELEGRAM_BUTTON_ICONS_ENABLED=False)
    async def test_no_screen_emits_a_custom_emoji_entity_in_the_body(self):
        """`custom_emoji` в тексте Bot API отклоняет вместе со всем сообщением."""
        screens = await self.build_every_screen()

        for name, (text, _keyboard) in screens.items():
            with self.subTest(screen=name):
                self.assertNotIn('tg-emoji', text)
                self.assertNotIn('custom_emoji', text)

    @override_settings(TELEGRAM_BUTTON_ICONS_ENABLED=False)
    async def test_callback_data_set_is_unchanged(self):
        screens = await self.build_every_screen()

        emitted = {
            _normalize(button.callback_data)
            for _text, keyboard in screens.values()
            for row in keyboard.inline_keyboard
            for button in row
            if button.callback_data
        }

        self.assertEqual(emitted, set(EXPECTED_CALLBACK_DATA))

    @override_settings(TELEGRAM_BUTTON_ICONS_ENABLED=False)
    async def test_screens_escape_markup_characters_in_values(self):
        """Режим HTML: неэкранированный `&` в ссылке ломает всё сообщение."""
        screens = await self.build_every_screen()

        self.assertIn('&amp;hl=ru', screens['faq'][0])
        self.assertNotIn('&hl=ru', screens['faq'][0])

    @override_settings(TELEGRAM_BUTTON_ICONS_ENABLED=False)
    async def test_profile_does_not_duplicate_the_subscription_links(self):
        """Профиль и «Подписки» показывали одни и те же ссылки; ссылки остались одни."""
        screens = await self.build_every_screen()

        self.assertIn('https://sub.example.test/sub/stable', screens['keys'][0])
        self.assertNotIn('https://sub.example.test/sub/stable', screens['profile'][0])

    @override_settings(TELEGRAM_BUTTON_ICONS_ENABLED=False)
    async def test_device_flows_render_the_subscriptions_screen(self):
        """Привязка и сброс возвращались сообщением без клавиатуры — тупиком."""
        text, keyboard = (await self.build_every_screen())['keys_notice']

        self.assertIn('Подписка подключена.', text)
        self.assertTrue(keyboard.inline_keyboard)

    @override_settings(TELEGRAM_BUTTON_ICONS_ENABLED=False)
    async def test_every_screen_still_shows_one_combined_balance(self):
        """Владелец просил разделить учёт, а не число: итог на экранах прежний."""
        self.split = BalanceSplit(real=Decimal('30.00'), bonus=Decimal('70.00'))
        screens = await self.build_every_screen()

        for name in ('profile', 'balance', 'keys'):
            with self.subTest(screen=name):
                self.assertIn('Баланс: 100 руб.', screens[name][0])
                self.assertNotIn('Баланс: 30', screens[name][0])

    @override_settings(TELEGRAM_BUTTON_ICONS_ENABLED=False)
    async def test_the_bonus_line_appears_only_where_it_answers_something(self):
        self.split = BalanceSplit(real=Decimal('30.00'), bonus=Decimal('70.00'))
        screens = await self.build_every_screen()

        self.assertIn('В том числе бонусных: 70.00 руб.', screens['profile'][0])
        self.assertIn('В том числе бонусных: 70.00 руб.', screens['balance'][0])
        # Экран подписок — про ссылки и действия над ними; денежная расшифровка
        # там не помогает ни одному решению пользователя.
        self.assertNotIn('бонусных', screens['keys'][0])

    @override_settings(TELEGRAM_BUTTON_ICONS_ENABLED=False)
    async def test_an_account_without_bonus_sees_no_extra_line(self):
        """Пустая строка «бонусов 0» — шум для того, кому ничего не дарили."""
        screens = await self.build_every_screen()

        for name in ('profile', 'balance'):
            with self.subTest(screen=name):
                self.assertNotIn('бонусных', screens[name][0])

    @override_settings(TELEGRAM_BUTTON_ICONS_ENABLED=False, BALANCE_SPLIT_UI_ENABLED=False)
    async def test_the_flag_removes_the_breakdown_and_leaves_the_total(self):
        self.split = BalanceSplit(real=Decimal('30.00'), bonus=Decimal('70.00'))
        screens = await self.build_every_screen()

        self.assertIn('Баланс: 100 руб.', screens['profile'][0])
        self.assertNotIn('бонусных', screens['profile'][0])

    @override_settings(TELEGRAM_BUTTON_ICONS_ENABLED=False)
    async def test_no_screen_offers_binding_as_a_user_chore(self):
        """Привязка происходит сама; кнопка обещала работу, которой нет."""
        screens = await self.build_every_screen()

        for name, (_text, keyboard) in screens.items():
            with self.subTest(screen=name):
                labels = [button.callback_data for row in keyboard.inline_keyboard for button in row]
                self.assertNotIn('bind_device', labels)


class IconCatalogTests(IsolatedAsyncioTestCase):
    def test_every_fallback_is_an_actual_emoji(self):
        for name, icon in icons.CATALOG.items():
            with self.subTest(icon=name):
                self.assertTrue(icons.is_emoji(icon.fallback), f'{name}: {icon.fallback!r} не эмодзи')

    def test_plain_signs_are_not_accepted_as_fallbacks(self):
        for symbol in ('+', '✓', '-', 'x', ''):
            with self.subTest(symbol=symbol):
                self.assertFalse(icons.is_emoji(symbol))

    def test_catalog_is_built_from_the_constants(self):
        """Каталог собирается интроспекцией, поэтому разойтись с ними не может."""
        self.assertEqual(icons.CATALOG['KEY'], icons.KEY)
        self.assertTrue(all(icon.set_name for icon in icons.CATALOG.values()))


class AnchorMessageTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        self.query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
        self.update = SimpleNamespace(callback_query=self.query, effective_chat=SimpleNamespace(id=1001))

    async def test_callback_edits_the_message_instead_of_sending_a_new_one(self):
        await render_screen(self.update, self.context, 'текст')

        self.query.edit_message_text.assert_awaited_once()
        self.context.bot.send_message.assert_not_awaited()

    async def test_start_sends_a_new_message(self):
        """Единственный случай, где редактировать нечего."""
        update = SimpleNamespace(callback_query=None, effective_chat=SimpleNamespace(id=1001))

        await render_screen(update, self.context, 'текст')

        self.context.bot.send_message.assert_awaited_once()

    async def test_forced_new_message_skips_the_edit(self):
        await render_screen(self.update, self.context, 'текст', force_new=True)

        self.query.edit_message_text.assert_not_awaited()
        self.context.bot.send_message.assert_awaited_once()

    async def test_unchanged_screen_does_not_surface_an_error(self):
        """Повторное нажатие той же кнопки — не ошибка пользователя."""
        self.query.edit_message_text.side_effect = BadRequest('Message is not modified')

        await render_screen(self.update, self.context, 'текст')

        self.context.bot.send_message.assert_not_awaited()

    async def test_message_too_old_to_edit_falls_back_to_sending(self):
        self.query.edit_message_text.side_effect = BadRequest("Message can't be edited")

        await render_screen(self.update, self.context, 'текст')

        self.context.bot.send_message.assert_awaited_once()

    async def test_unknown_bad_request_is_not_swallowed(self):
        self.query.edit_message_text.side_effect = BadRequest('Chat not found')

        with self.assertRaises(BadRequest):
            await render_screen(self.update, self.context, 'текст')

    async def test_toast_confirms_an_action_the_screen_barely_shows(self):
        """Успешное действие с почти прежним экраном читается как несработавшее."""
        await render_screen(self.update, self.context, 'текст', toast='Подписка добавлена.')

        self.query.answer.assert_awaited_once_with(text='Подписка добавлена.')

    async def test_double_answer_does_not_break_the_render(self):
        """Обработчик мог ответить своим текстом раньше — второй ответ отклоняется."""
        self.query.answer.side_effect = BadRequest('Query is too old')

        await render_screen(self.update, self.context, 'текст')

        self.query.edit_message_text.assert_awaited_once()
