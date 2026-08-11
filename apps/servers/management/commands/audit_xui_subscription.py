import asyncio
from dataclasses import dataclass

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.servers.models import Server
from utils.py3xui.async_api import AsyncApi


@dataclass(frozen=True)
class SubscriptionClientCounts:
    total: int
    with_sub_id: int
    enabled: int


async def fetch_subscription_client_counts(server: Server) -> SubscriptionClientCounts:
    """Read subscription readiness from the legacy inbound without mutations."""
    api = AsyncApi(server.vpn_url, server.vpn_username, server.vpn_password)
    await api.login()
    inbound = await api.inbound.get_by_id(server.inbound_id)
    clients = inbound.settings.clients
    return SubscriptionClientCounts(
        total=len(clients),
        with_sub_id=sum(bool(client.sub_id) for client in clients),
        enabled=sum(bool(client.enable) for client in clients),
    )


class Command(BaseCommand):
    help = 'Read-only readiness audit for the staged 3x-ui subscription connector.'

    def handle(self, *args, **options):
        servers = list(Server.objects.order_by('id'))
        if not servers:
            raise CommandError('Subscription readiness audit found no configured servers.')

        self.stdout.write(f'connector_enabled={settings.SUBSCRIPTION_CONNECTOR_ENABLED}')
        self.stdout.write(f'base_url_configured={bool(settings.SUBSCRIPTION_BASE_URL)}')

        for server in servers:
            try:
                counts = asyncio.run(fetch_subscription_client_counts(server))
            except Exception:
                raise CommandError(f'Subscription readiness audit failed for server_id={server.id}.') from None
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

        self.stdout.write(self.style.SUCCESS('Subscription readiness audit completed (read-only).'))
