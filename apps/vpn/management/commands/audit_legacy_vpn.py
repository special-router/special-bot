import asyncio
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce

from apps.servers.models import Server
from apps.vpn.models import UserVPN
from utils.py3xui.async_api import AsyncApi


async def fetch_control_plane_client_ids(server: Server) -> tuple[set[str], set[str]]:
    """Return all and enabled client UUIDs without changing 3x-ui."""
    api = AsyncApi(
        server.vpn_url,
        server.vpn_username,
        server.vpn_password,
        use_tls_verify=False,
    )
    await api.login()
    inbound = await api.inbound.get_by_id(server.inbound_id)
    all_ids = {str(client.id) for client in inbound.settings.clients}
    enabled_ids = {str(client.id) for client in inbound.settings.clients if client.enable}
    return all_ids, enabled_ids


def get_server_entitlement(server: Server) -> tuple[int, set[str]]:
    """Return record count and UUIDs entitled by the deployed balance rule."""
    records = list(
        UserVPN.objects.filter(server_id=server.id).annotate(
            entitlement_balance=Coalesce(
                Sum('user__transactions__amount'),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        )
    )
    entitled = {str(user_vpn.vpn_uuid) for user_vpn in records if user_vpn.entitlement_balance >= server.tariff.price}
    return len(records), entitled


class Command(BaseCommand):
    help = 'Read-only audit of legacy VPN entitlement against 3x-ui control-plane clients.'

    def handle(self, *args, **options):
        missing_total = 0
        servers = list(Server.objects.select_related('tariff').order_by('id'))
        if not servers:
            raise CommandError('Legacy VPN audit found no configured servers.')

        for server in servers:
            records_total, entitled_ids = get_server_entitlement(server)
            try:
                control_plane_ids, enabled_control_plane_ids = asyncio.run(fetch_control_plane_client_ids(server))
            except Exception:
                raise CommandError(f'Legacy VPN audit failed for server_id={server.id}.') from None

            missing_ids = entitled_ids - enabled_control_plane_ids
            extra_ids = control_plane_ids - entitled_ids
            missing_total += len(missing_ids)

            self.stdout.write(
                ' '.join(
                    (
                        f'server_id={server.id}',
                        f'records={records_total}',
                        f'entitled={len(entitled_ids)}',
                        f'control_plane={len(control_plane_ids)}',
                        f'control_plane_enabled={len(enabled_control_plane_ids)}',
                        f'entitled_missing={len(missing_ids)}',
                        f'extras={len(extra_ids)}',
                    )
                )
            )

        if missing_total:
            raise CommandError(f'Legacy VPN audit found entitled_missing={missing_total}.')

        self.stdout.write(self.style.SUCCESS('Legacy VPN audit passed.'))
