"""Static guarantees for the UI walker, so a broken harness fails here first.

The walk itself needs Telegram and a logged-in account, and neither belongs in
the test suite.  What is checked here is everything that must hold before a
single button is pressed: the module imports without Telethon installed, the
default run is non-mutating, and no code path can put a session path or an api
hash into the report.
"""

import io
import re
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from ops.scripts import verify_bot_ui as harness


class ArgumentTests(unittest.TestCase):
    def test_the_module_imports_without_telethon(self):
        """Telethon живёт в другом venv; --help обязан работать и без него."""
        self.assertNotIn('telethon', harness.__dict__)

    def test_defaults_name_the_intended_profile_and_bot(self):
        args = harness.build_parser().parse_args([])

        self.assertEqual(args.profile, harness.DEFAULT_PROFILE)
        self.assertEqual(args.bot, harness.DEFAULT_BOT)
        self.assertFalse(args.allow_mutations)

    def test_mutations_stay_behind_an_explicit_flag(self):
        self.assertTrue(harness.build_parser().parse_args(['--allow-mutations']).allow_mutations)

    def test_a_non_positive_timeout_is_refused(self):
        self.assertEqual(harness.main(['--timeout', '0']), 2)

    def test_listing_actions_needs_neither_credentials_nor_network(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(harness.main(['--list-actions']), 0)

        self.assertIn('add_key:', buffer.getvalue())


class ClassificationTests(unittest.TestCase):
    def test_money_and_data_actions_are_mutating(self):
        for data in (
            'add_key:12345678',
            'remove_key:7',
            'top_up_balance_promo',
            'top_up_balance_one_month',
            'reset_devices',
            'support_open',
        ):
            with self.subTest(callback_data=data):
                self.assertTrue(harness.is_mutating(data))

    def test_navigation_is_not_mutating(self):
        for data in ('main_menu', 'show_keys', 'show_balance', 'profile', 'referral', 'faq', 'show_keys_for_remove'):
            with self.subTest(callback_data=data):
                self.assertFalse(harness.is_mutating(data))

    def test_the_one_time_nonce_does_not_defeat_deduplication(self):
        self.assertEqual(harness.normalize('add_key:12345678'), harness.normalize('add_key:99999999'))

    def test_the_screen_name_carries_the_whole_path(self):
        self.assertEqual(
            harness.screen_name(('show_keys', 'add_key:12345678')),
            '/start > show_keys > add_key:*',
        )


class ScreenCheckTests(unittest.TestCase):
    def _screen(self, text, markup=object()):
        return SimpleNamespace(text=text, reply_markup=markup)

    def test_the_error_handler_text_on_a_screen_is_a_failure(self):
        screen = self._screen('Не удалось выполнить действие. Сбой записан, попробуйте повторить через минуту.')

        self.assertIn('отказе', harness.check_screen(screen))

    def test_a_disabled_provider_is_a_note_and_not_a_failure(self):
        """Иначе гейт был бы красным всё время, пока провайдер не подключён."""
        screen = self._screen('Оплата\n\nПополнение временно недоступно: платёжный провайдер не подключён.')

        self.assertEqual(harness.check_screen(screen), '')
        self.assertIn('Пополнение временно недоступно', harness.notices_of(screen))

    def test_a_screen_without_a_keyboard_is_a_dead_end(self):
        self.assertIn('клавиатуры', harness.check_screen(self._screen('Подписки', markup=None)))

    def test_an_empty_screen_is_a_failure(self):
        self.assertIn('пустым', harness.check_screen(self._screen('   ')))


class CredentialTests(unittest.TestCase):
    def test_an_invalid_profile_name_never_reaches_the_filesystem(self):
        with self.assertRaises(harness.ProfileUnusable):
            harness.load_credentials('../../etc/passwd')

    def test_a_missing_profile_is_blocked_by_name_only(self):
        with self.assertRaises(harness.ProfileUnusable) as raised:
            harness.load_credentials('nonexistent_profile_for_tests')

        message = str(raised.exception)
        self.assertIn('nonexistent_profile_for_tests', message)
        self.assertNotIn(str(Path.home()), message)
        self.assertNotIn('sessions', message)

    def test_no_secret_bearing_name_is_ever_printed(self):
        """Отчёт и блокировки печатаются пользователю; сессии в них быть не может."""
        source = Path(harness.__file__).read_text(encoding='utf-8')
        printed = re.findall(r'^\s*print\((.*)$', source, flags=re.MULTILINE)

        for line in printed:
            with self.subTest(line=line.strip()):
                for forbidden in ('session', 'api_hash', 'api_id', 'credentials', 'phone'):
                    self.assertNotIn(forbidden, line)


if __name__ == '__main__':
    unittest.main()
