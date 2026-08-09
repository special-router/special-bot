import asyncio

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.servers.models import Server
from apps.servers.subscription_connector import XUISubscriptionConnector
from apps.vpn.management.commands.audit_legacy_vpn import get_server_entitlement
from apps.vpn.models import UserVPN


class Command(BaseCommand):
    help = 'Prepare missing 3x-ui subIds only with explicit --apply and enabled connector.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true', help='Assign missing subIds; omitted means read-only dry run.'
        )
        parser.add_argument('--server-id', type=int, help='Limit to one configured Server record.')
        parser.add_argument(
            '--user-vpn-id', type=int, help='Explicit internal canary UserVPN record; required with --apply.'
        )

    def handle(self, *args, **options):
        if options['apply'] and not settings.SUBSCRIPTION_CONNECTOR_ENABLED:
            raise CommandError('Refusing --apply: SUBSCRIPTION_CONNECTOR_ENABLED is false.')
        if options['apply'] and (not options['server_id'] or not options['user_vpn_id']):
            raise CommandError('Refusing --apply: --server-id and --user-vpn-id are required for one explicit canary.')

        servers = Server.objects.order_by('id')
        if options['server_id']:
            servers = servers.filter(id=options['server_id'])
        servers = list(servers)
        if not servers:
            raise CommandError('No matching configured servers.')

        for server in servers:
            records = UserVPN.objects.filter(server=server).order_by('created_at')
            if options['user_vpn_id']:
                records = records.filter(id=options['user_vpn_id'])
            if options['apply']:
                records = records.filter(enabled=True)
            records = list(records.select_related('user', 'server'))
            if options['user_vpn_id'] and len(records) != 1:
                mode = '--apply' if options['apply'] else 'dry-run'
                raise CommandError(f'Refusing {mode}: explicit record was not found on this server.')
            if options['apply']:
                _, entitled_ids = get_server_entitlement(server)
                if str(records[0].vpn_uuid) not in entitled_ids:
                    raise CommandError('Refusing --apply: explicit record is not balance-entitled.')
            if not options['apply']:
                self.stdout.write(f'server_id={server.id} candidates={len(records)} mode=dry-run changes=0')
                continue

            prepared = 0
            connector = XUISubscriptionConnector(server)
            for user_vpn in records:
                asyncio.run(connector.ensure_subscription_reference(user_vpn))
                prepared += 1
                self.stdout.write(f'server_id={server.id} prepared=yes')

            self.stdout.write(f'server_id={server.id} candidates={len(records)} mode=apply prepared={prepared}')

        self.stdout.write(self.style.SUCCESS('3x-ui subscription preparation completed.'))
