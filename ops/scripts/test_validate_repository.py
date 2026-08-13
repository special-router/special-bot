import unittest

from ops.scripts.validate_repository import dependency_drift, flag_drift, GUARDED_RUNTIME_DEPENDENCIES, REQUIREMENTS


SETTINGS = """
DEBUG = env.bool('DEBUG', False)
SUPPORT_CHAT_ID = env.int('SUPPORT_CHAT_ID', 0)
SUBSCRIPTION_INTERNAL_ENDPOINTS = _internal_canary_json(
    'SUBSCRIPTION_INTERNAL_ENDPOINTS')
"""

FLAGS = """
| Setting | Type | Default | Prod | What it does |
|---|---|---|---|---|
| `DEBUG` | bool | `False` | `false` | Django debug mode. |
| `SUPPORT_CHAT_ID` | int | `0` | `0` | Operators' supergroup. |
| `SUBSCRIPTION_INTERNAL_ENDPOINTS` | json | `[]` | `[]` | Canary endpoints. |
"""

REQUIREMENTS_TEXT = """
httpx==0.28.1
    # via
    #   py3xui
    #   python-telegram-bot
py3xui==0.5.1
    # via vpnbot (pyproject.toml)
"""


class FlagDriftTests(unittest.TestCase):
    def test_matching_inventories_report_no_drift(self):
        self.assertEqual(flag_drift(SETTINGS, FLAGS), ([], []))

    def test_multiline_and_helper_settings_are_recognised(self):
        _, unknown = flag_drift(SETTINGS, FLAGS)
        self.assertNotIn('SUBSCRIPTION_INTERNAL_ENDPOINTS', unknown)

    def test_a_new_setting_without_a_row_fails(self):
        settings = SETTINGS + "NEW_FEATURE_ENABLED = env.bool('NEW_FEATURE_ENABLED', False)\n"
        undocumented, unknown = flag_drift(settings, FLAGS)
        self.assertEqual(undocumented, ['NEW_FEATURE_ENABLED'])
        self.assertEqual(unknown, [])

    def test_a_row_for_a_removed_setting_fails(self):
        flags = FLAGS + '| `RETIRED_FLAG` | bool | `False` | `false` | Gone. |\n'
        undocumented, unknown = flag_drift(SETTINGS, flags)
        self.assertEqual(undocumented, [])
        self.assertEqual(unknown, ['RETIRED_FLAG'])

    def test_prose_mentioning_a_flag_is_not_a_row(self):
        flags = FLAGS + 'Set `RETIRED_FLAG` only after the owner approves.\n'
        self.assertEqual(flag_drift(SETTINGS, flags), ([], []))


class DependencyDriftTests(unittest.TestCase):
    def test_the_pinned_version_reports_no_drift(self):
        self.assertEqual(dependency_drift(REQUIREMENTS_TEXT, {'py3xui': '0.5.1'}), [])

    def test_another_version_names_both_the_pin_and_the_interpreter(self):
        (message,) = dependency_drift(REQUIREMENTS_TEXT, {'py3xui': '0.7.0'})
        self.assertIn('0.5.1', message)
        self.assertIn('0.7.0', message)

    def test_an_absent_dependency_is_not_drift(self):
        # The validator also runs on a bare interpreter that installs nothing.
        self.assertEqual(dependency_drift(REQUIREMENTS_TEXT, {'py3xui': None}), [])

    def test_an_unpinned_dependency_fails(self):
        drift = dependency_drift('py3xui\n', {'py3xui': '0.5.1'})
        self.assertEqual(drift, ['py3xui is not pinned in requirements.txt'])

    def test_a_via_comment_is_not_a_pin(self):
        drift = dependency_drift('    # via py3xui==9.9.9\n', {'py3xui': '0.5.1'})
        self.assertEqual(drift, ['py3xui is not pinned in requirements.txt'])

    def test_every_guarded_dependency_is_pinned_in_the_real_requirements(self):
        text = REQUIREMENTS.read_text(encoding='utf-8')
        self.assertEqual(dependency_drift(text, dict.fromkeys(GUARDED_RUNTIME_DEPENDENCIES)), [])


if __name__ == '__main__':
    unittest.main()
