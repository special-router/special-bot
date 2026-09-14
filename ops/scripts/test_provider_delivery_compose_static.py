import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = (ROOT / 'docker-compose.deploy.yml').read_text(encoding='utf-8')
SOURCE_KEYS = (
    'SUBSCRIPTION_BACKUP_UPSTREAM_URLS',
    'SUBSCRIPTION_BACKUP_UPSTREAM_HWID',
    'SUBSCRIPTION_BACKUP_UPSTREAM_DEVICE_OS',
    'SUBSCRIPTION_BACKUP_UPSTREAM_OS_VERSION',
    'SUBSCRIPTION_BACKUP_UPSTREAM_DEVICE_MODEL',
    'SUBSCRIPTION_BACKUP_UPSTREAM_USER_AGENT',
)


def service_block(name: str) -> str:
    marker = f'\n  {name}:\n'
    start = COMPOSE.index(marker) + len(marker)
    end = COMPOSE.find('\n  ', start)
    while end != -1 and COMPOSE[end + 3:end + 4].isspace():
        end = COMPOSE.find('\n  ', end + 1)
    return COMPOSE[start:] if end == -1 else COMPOSE[start:end]


class ProviderDeliveryComposeTests(unittest.TestCase):
    def test_customer_facing_workers_clear_provider_source_environment(self):
        for key in SOURCE_KEYS:
            self.assertIn(f'  {key}: ""', COMPOSE)
        for service in ('web', 'celery', 'broadcast', 'celery_beat'):
            self.assertIn('<<: *no-provider-source', service_block(service))

    def test_ingestion_and_monitoring_keep_the_secret_file(self):
        for service in ('monitoring', 'provider_ingest'):
            block = service_block(service)
            self.assertNotIn('<<: *no-provider-source', block)
            self.assertIn(
                'SUBSCRIPTION_BACKUP_SECRET_FILE: /run/secrets/subscription-backup.json',
                block,
            )


if __name__ == '__main__':
    unittest.main()
