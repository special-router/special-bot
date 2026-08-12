import unittest

from ops.scripts.validate_repository import flag_drift


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


if __name__ == '__main__':
    unittest.main()
