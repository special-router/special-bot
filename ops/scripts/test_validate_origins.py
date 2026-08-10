import unittest

from ops.origins import validate_origins


class OriginValidationTests(unittest.TestCase):
    def row(self, **updates):
        value = {
            'id': 'primary',
            'provider': 'a',
            'asn': 64500,
            'region': 'a',
            'public_host': 'a.example.invalid',
            'health_url': 'https://a.example.invalid/health',
            'transport': 'vless-reality-tcp',
            'priority': 10,
            'rollout_state': 'production',
            'role': 'primary',
            'enabled': True,
        }
        value.update(updates)
        return value

    def test_one_origin_is_valid_but_not_independent(self):
        self.assertFalse(validate_origins([self.row()])['independent_origins_configured'])

    def test_distinct_provider_asn_is_independent_config(self):
        secondary = self.row(
            id='secondary', provider='b', asn=64501, region='b', public_host='b.example.invalid',
            health_url='https://b.example.invalid/health', role='secondary', priority=20,
        )
        self.assertTrue(validate_origins([self.row(), secondary])['independent_origins_configured'])

    def test_bearer_like_query_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_origins([self.row(health_url='https://a.example.invalid/health?token=value')])

    def test_same_provider_asn_is_not_independent(self):
        secondary = self.row(
            id='secondary', role='secondary', public_host='b.example.invalid',
            health_url='https://b.example.invalid/health', priority=20,
        )
        self.assertFalse(validate_origins([self.row(), secondary])['independent_origins_configured'])

    def test_enabled_disabled_state_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_origins([self.row(rollout_state='disabled')])


if __name__ == '__main__':
    unittest.main()
