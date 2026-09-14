from django.core.management.base import BaseCommand, CommandError

from apps.subscriptions.provider_snapshots import (
    enabled_provider_ids,
    provider_snapshot_status,
    refresh_provider_snapshots,
)


class Command(BaseCommand):
    help = 'Refresh immutable provider delivery snapshots without printing secrets.'

    def add_arguments(self, parser):
        parser.add_argument('--require-all', action='store_true')

    def handle(self, *args, **options):
        results = refresh_provider_snapshots()
        status = provider_snapshot_status()
        expected = set(enabled_provider_ids())
        failed = sorted(provider_id for provider_id in expected if not results.get(provider_id, {}).get('ok'))
        self.stdout.write(
            f'provider_snapshots ready={status.ready_sources}/{status.configured_sources} '
            f'refreshed={sum(bool(item.get("ok")) for item in results.values())} failed={len(failed)}'
        )
        if options['require_all'] and (failed or not status.ready):
            raise CommandError('provider_snapshot_refresh_incomplete')
