"""Fail loudly when configured inbound ids do not exist on the panel.

Mirror and status synchronization both tolerate per-inbound errors so a single
bad inbound cannot break billing. That tolerance previously hid a permanently
misconfigured id: every call failed and nothing reported it. This read-only
command turns that silence into an explicit, greppable result.
"""
from __future__ import annotations

import asyncio

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.servers.models import Server
from utils.py3xui.async_api import AsyncApi


class Command(BaseCommand):
    help = 'Verify that configured mirror/status/primary inbound ids exist on the panel.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--strict', action='store_true',
            help='Exit non-zero when any configured inbound id is missing.',
        )

    def handle(self, *args, **options):
        # Read the server synchronously; the async ORM would open a second
        # connection and deadlock the sqlite test database.
        server_id = getattr(settings, 'SPECIAL_MONITOR_SERVER_ID', 1) or 1
        server = Server.objects.get(id=server_id)
        asyncio.run(self._run(
            options['strict'],
            (server.vpn_url, server.vpn_username, server.vpn_password),
            int(server.inbound_id),
        ))

    async def _run(self, strict: bool, credentials: tuple[str, str, str], primary_id: int) -> None:
        api = AsyncApi(*credentials)
        await api.login()
        existing = {int(inbound.id) for inbound in await api.inbound.get_list()}

        mirror_ids = [int(i) for i in (getattr(settings, 'MIRROR_INBOUND_IDS', []) or [])]
        status_id = int(getattr(settings, 'STATUS_INBOUND_ID', 0) or 0)

        configured: list[tuple[str, int]] = [('primary', primary_id)]
        configured += [('mirror', i) for i in mirror_ids]
        if status_id:
            configured.append(('status', status_id))

        missing = []
        for role, inbound_id in configured:
            present = inbound_id in existing
            self.stdout.write(f'{role}_inbound={inbound_id} exists={str(present).lower()}')
            if not present:
                missing.append((role, inbound_id))

        self.stdout.write(f'panel_inbounds={len(existing)} configured={len(configured)} missing={len(missing)}')
        if missing and strict:
            names = ','.join(f'{role}:{inbound_id}' for role, inbound_id in missing)
            raise CommandError(f'configured inbound ids missing from panel: {names}')
