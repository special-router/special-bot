import unittest

from ops.scripts.validate_repository import (
    dependency_drift,
    dependency_pin_report,
    flag_drift,
    pinned_requirements,
    REQUIREMENTS,
)


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


class PinnedRequirementsTests(unittest.TestCase):
    def test_only_unindented_lines_are_pins(self):
        self.assertEqual(pinned_requirements(REQUIREMENTS_TEXT), {'httpx': '0.28.1', 'py3xui': '0.5.1'})

    def test_a_via_comment_naming_a_version_is_not_a_pin(self):
        self.assertEqual(pinned_requirements('    # via py3xui==9.9.9\n'), {})

    def test_an_environment_marker_is_not_part_of_the_version(self):
        self.assertEqual(pinned_requirements("colorama==0.4.6 ; sys_platform == 'win32'\n"), {'colorama': '0.4.6'})

    def test_the_real_requirements_pin_the_whole_image(self):
        pinned = pinned_requirements(REQUIREMENTS.read_text(encoding='utf-8'))
        self.assertEqual(pinned['py3xui'], '0.5.1')
        self.assertGreater(len(pinned), 40)


class DependencyDriftTests(unittest.TestCase):
    PINNED = {'httpx': '0.28.1', 'py3xui': '0.5.1'}

    def test_the_pinned_versions_report_no_drift(self):
        self.assertEqual(dependency_drift(self.PINNED, {'httpx': '0.28.1', 'py3xui': '0.5.1'}), [])

    def test_one_drifted_dependency_names_only_itself_and_both_versions(self):
        (message,) = dependency_drift(self.PINNED, {'httpx': '0.28.1', 'py3xui': '0.7.0'})
        self.assertIn('py3xui', message)
        self.assertIn('0.5.1', message)
        self.assertIn('0.7.0', message)
        self.assertNotIn('httpx', message)

    def test_every_drifted_dependency_gets_its_own_line(self):
        drift = dependency_drift(self.PINNED, {'httpx': '0.29.0', 'py3xui': '0.7.0'})
        self.assertEqual(len(drift), 2)

    def test_an_absent_dependency_is_not_drift(self):
        # The validator also runs on a bare interpreter that installs nothing.
        self.assertEqual(dependency_drift(self.PINNED, {'httpx': None, 'py3xui': None}), [])


class DependencyPinReportTests(unittest.TestCase):
    """The interpreter running these tests installs requirements.txt, by construction."""

    def test_this_interpreter_matches_every_pin_it_has(self):
        summary, failure = dependency_pin_report()
        self.assertIsNone(failure)
        verified, pinned = summary.split('/')
        self.assertEqual(pinned, str(len(pinned_requirements(REQUIREMENTS.read_text(encoding='utf-8')))))
        self.assertGreater(int(verified), 0)


if __name__ == '__main__':
    unittest.main()
