"""Give the clients that already exist in one inbound their attribution label.

New writes are labelled by ``apps.servers.client_labels``; the clients written
before it existed carry an empty ``email`` and accumulate no traffic statistics
at all.  This command repairs exactly one explicitly named inbound.  It reads
every inbound, because ``client_traffics.email`` is UNIQUE across the whole
panel database and a collision has to be seen before it is written, but it
mutates only the inbound it was given.
"""
import asyncio

from django.core.management.base import BaseCommand, CommandError

from apps.servers.client_labels import client_label, is_client_label, labelling_enabled
from apps.servers.models import Server
from apps.vpn.models import UserVPN
from utils.py3xui.async_api import AsyncApi


def _short(client_uuid: str) -> str:
    """Panel identities never leave this process in full."""
    return str(client_uuid)[:8]


class Command(BaseCommand):
    help = 'Backfill 3x-ui attribution labels on one inbound; dry run unless --apply.'

    def add_arguments(self, parser):
        parser.add_argument('--server-id', type=int, required=True, help='Configured Server record holding the panel.')
        parser.add_argument('--inbound-id', type=int, required=True, help='The single inbound to label.')
        parser.add_argument('--apply', action='store_true', help='Write the labels; omitted means read-only dry run.')
        parser.add_argument('--list', action='store_true', help='Print one line per client that would change.')

    def handle(self, *args, **options):
        if options['apply'] and not labelling_enabled():
            # A label written now would be dropped the next time the transport
            # touches that client, leaving the panel and its traffic rows out of
            # step with each other.  Reading is still useful; writing is not.
            raise CommandError('Refusing --apply: CLIENT_TRAFFIC_LABELS_ENABLED is false.')
        # Every database read happens here, before the event loop: the panel
        # calls are the only thing that needs to be async.
        server, owners = self._load(options['server_id'])
        # Ownership comes from configuration, never from the argument: the panel
        # also hosts a foreign tenant's inbounds, which we do not write to.
        if options['inbound_id'] != server.inbound_id:
            raise CommandError(
                f'Refusing: inbound {options["inbound_id"]} is not the primary inbound '
                f'of server {server.id} ({server.inbound_id}).'
            )
        asyncio.run(self._run(options, server, owners))

    async def _run(self, options, server: Server, owners: dict[str, tuple[int, int]]) -> None:
        api = AsyncApi(server.vpn_url, server.vpn_username, server.vpn_password)
        await api.login()
        inbounds = await api.inbound.get_list()

        inbound_id = options['inbound_id']
        target = next((item for item in inbounds if int(item.id) == inbound_id), None)
        if target is None:
            raise CommandError(f'Inbound {inbound_id} is not present on this panel.')

        labels_in_use, traffic_rows = self._labels_in_use(inbounds)

        planned: list[tuple[str, str, object]] = []
        collisions: list[str] = []
        ownerless = 0
        already = 0
        foreign = 0

        for client in (target.settings.clients or []):
            uuid = str(client.id)
            owner = owners.get(uuid)
            if owner is None or owner[1] != inbound_id:
                # No verified owner, or an owner whose server configures a
                # different inbound: compatibility-only clients are never mutated.
                ownerless += 1
                continue
            label = client_label(inbound_id, owner[0])
            current = client.email or ''
            if current == label:
                already += 1
                continue
            if current and not is_client_label(current):
                foreign += 1
                continue
            holder = labels_in_use.get(label)
            if holder is not None and holder != (inbound_id, uuid):
                collisions.append(f'label={label} held_by=inbound={holder[0]} client={_short(holder[1])}')
                continue
            if label in traffic_rows:
                collisions.append(f'label={label} held_by=client_traffics inbound={traffic_rows[label]}')
                continue
            planned.append((uuid, label, client))

        mode = 'apply' if options['apply'] else 'dry-run'
        if options['list']:
            for uuid, label, _ in planned:
                self.stdout.write(f'client={_short(uuid)} label={label}')
        for collision in collisions:
            self.stdout.write(f'collision {collision}')

        if collisions and options['apply']:
            raise CommandError(f'Refusing --apply: {len(collisions)} label collisions; nothing was written.')

        changed = 0
        if options['apply']:
            for uuid, label, client in planned:
                client.email = label
                client.inbound_id = inbound_id
                await api.client.update(uuid, client)
                changed += 1

        self.stdout.write(
            f'inbound={inbound_id} mode={mode} clients={len(target.settings.clients or [])} '
            f'planned={len(planned)} changed={changed} already_labelled={already} '
            f'skipped_ownerless={ownerless} skipped_foreign_label={foreign} collisions={len(collisions)}'
        )

    @staticmethod
    def _load(server_id: int) -> tuple[Server, dict[str, tuple[int, int]]]:
        try:
            server = Server.objects.get(id=server_id)
        except Server.DoesNotExist as error:
            raise CommandError(f'No configured server with id={server_id}.') from error
        owners: dict[str, tuple[int, int]] = {}
        # Lowest id wins, matching how the runtime resolver picks an owner.  The
        # second element is the inbound that owner's server configures.
        rows = UserVPN.objects.order_by('id').values_list('id', 'vpn_uuid', 'server__inbound_id')
        for user_vpn_id, vpn_uuid, primary_inbound_id in rows:
            if primary_inbound_id is not None:
                owners.setdefault(str(vpn_uuid), (user_vpn_id, primary_inbound_id))
        return server, owners

    @staticmethod
    def _labels_in_use(inbounds) -> tuple[dict[str, tuple[int, str]], dict[str, int]]:
        """Map every label the panel already holds to whoever holds it.

        Two separate sources: a client entry in some inbound's configuration,
        and a ``client_traffics`` row, which can outlive the client that made it
        and still occupy the label.
        """
        clients: dict[str, tuple[int, str]] = {}
        traffic_rows: dict[str, int] = {}
        for inbound in inbounds:
            for client in (inbound.settings.clients or []):
                if client.email:
                    clients.setdefault(client.email, (int(inbound.id), str(client.id)))
            for stat in (inbound.client_stats or []):
                if stat.email:
                    traffic_rows.setdefault(stat.email, int(inbound.id))
        return clients, traffic_rows
