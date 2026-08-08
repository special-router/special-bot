import asyncio

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.servers.management.commands.audit_xui_subscription import (
    fetch_subscription_client_counts,
)
from apps.servers.models import Server


class Command(BaseCommand):
    help = 'Read-only aggregate audit of 3x-ui subId coverage.'

    def add_arguments(self, parser):
        parser.add_argument('--server-id', type=int, help='Audit one explicit Server record.')

    def handle(self, *args, **options):
        servers = Server.objects.order_by('id')
        if options.get('server_id'):
            servers = servers.filter(id=options['server_id'])
        servers = list(servers)
        if not servers:
            raise CommandError('No matching configured servers.')

        self.stdout.write(f'connector_enabled={settings.SUBSCRIPTION_CONNECTOR_ENABLED}')
        self.stdout.write(f'base_url_configured={bool(settings.SUBSCRIPTION_BASE_URL)}')
        for server in servers:
            try:
                counts = asyncio.run(fetch_subscription_client_counts(server))
            except Exception:
                raise CommandError(
                    f'SubId coverage audit failed for server_id={server.id}.'
                ) from None
            self.stdout.write(
                ' '.join(
                    (
                        f'server_id={server.id}',
                        f'clients={counts.total}',
                        f'enabled={counts.enabled}',
                        f'with_sub_id={counts.with_sub_id}',
                        f'missing_sub_id={counts.total - counts.with_sub_id}',
                    )
                )
            )
        self.stdout.write(self.style.SUCCESS('SubId coverage audit completed (read-only).'))
