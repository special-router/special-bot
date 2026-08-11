import asyncio
import json
from dataclasses import asdict, dataclass

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.servers.models import Server
from utils.py3xui.async_api import AsyncApi


@dataclass(frozen=True)
class InboundSnapshot:
    server_id: int
    server_name: str
    inbound_id: int
    port: int
    protocol: str
    network: str
    security: str
    clients: int
    enabled_clients: int
    with_sub_id: int
    missing_sub_id: int


async def _fetch_inbound_snapshots_once(server: Server) -> list[InboundSnapshot]:
    """Read one normalized inbound inventory snapshot without changing 3x-ui."""
    api = AsyncApi(server.vpn_url, server.vpn_username, server.vpn_password)
    await api.login()
    inbounds = await api.inbound.get_list()
    snapshots = []
    for inbound in inbounds:
        clients = list(inbound.settings.clients or [])
        snapshots.append(
            InboundSnapshot(
                server_id=server.id,
                server_name=server.name,
                inbound_id=int(inbound.id),
                port=int(inbound.port),
                protocol=str(inbound.protocol),
                network=str(inbound.stream_settings.network),
                security=str(inbound.stream_settings.security),
                clients=len(clients),
                enabled_clients=sum(bool(client.enable) for client in clients),
                with_sub_id=sum(bool(client.sub_id) for client in clients),
                missing_sub_id=sum(not bool(client.sub_id) for client in clients),
            )
        )
    return sorted(snapshots, key=lambda item: (item.server_id, item.port, item.inbound_id))


async def fetch_inbound_snapshots(server: Server) -> list[InboundSnapshot]:
    """Return a stable normalized inbound inventory snapshot.

    Two consecutive equal reads are required so a briefly incomplete 3x-ui
    inventory cannot be evaluated as control-plane drift.
    """
    max_attempts = settings.XUI_CONTROL_PLANE_READ_ATTEMPTS
    backoff = settings.XUI_CONTROL_PLANE_READ_BACKOFF
    if max_attempts < 2:
        raise RuntimeError('Control plane consistency requires at least two read attempts.')

    previous = None
    for attempt in range(1, max_attempts + 1):
        try:
            current = await _fetch_inbound_snapshots_once(server)
            if previous is not None and current == previous:
                return current
            previous = current
        except Exception:
            previous = None
            if attempt == max_attempts:
                raise
        if attempt < max_attempts:
            await asyncio.sleep(backoff)

    raise RuntimeError(
        f'Control plane inventory consistency could not be established for server_id={server.id} '
        f'after {max_attempts} attempts.'
    )


class Command(BaseCommand):
    help = 'Read-only inventory of 3x-ui inbounds and client state.'

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true', dest='as_json', help='Emit JSONL records for monitoring.')
        parser.add_argument('--server-id', type=int, help='Limit to one configured Server record.')

    def handle(self, *args, **options):
        servers = Server.objects.order_by('id')
        if options['server_id']:
            servers = servers.filter(id=options['server_id'])
        servers = list(servers)
        if not servers:
            raise CommandError('Inbound audit found no matching configured servers.')

        snapshots = []
        for server in servers:
            try:
                snapshots.extend(asyncio.run(fetch_inbound_snapshots(server)))
            except Exception:
                raise CommandError(f'Inbound audit failed for server_id={server.id}.') from None

        if options['as_json']:
            for snapshot in snapshots:
                self.stdout.write(json.dumps(asdict(snapshot), sort_keys=True))
            return

        for snapshot in snapshots:
            self.stdout.write(
                ' '.join(
                    (
                        f'server_id={snapshot.server_id}',
                        f'inbound_id={snapshot.inbound_id}',
                        f'port={snapshot.port}',
                        f'protocol={snapshot.protocol}',
                        f'network={snapshot.network}',
                        f'security={snapshot.security}',
                        f'clients={snapshot.clients}',
                        f'enabled={snapshot.enabled_clients}',
                        f'with_sub_id={snapshot.with_sub_id}',
                        f'missing_sub_id={snapshot.missing_sub_id}',
                    )
                )
            )
        self.stdout.write(self.style.SUCCESS('Inbound audit completed (read-only).'))
