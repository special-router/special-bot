"""Sync 3x-ui client expiryTime and status label from balance so subscription
clients (happ) display how many days remain or that the subscription has ended.

The 3x-ui subscription remark is built as ``<inbound.remark>-<client.email>``,
so the per-client status is carried in the ``email`` field:

* balance covers at least one day  -> ``осталось N дней`` and expiryTime = now + N*d
* balance cannot cover one day        -> ``подписка окончена`` and the client is disabled

This command does not create billing transactions; daily billing and the
authoritative disable are owned by ``update_user_vpn``. This command mirrors the
status to every inbound in ``MIRROR_INBOUND_IDS`` so all endpoints agree.
"""
from __future__ import annotations

import asyncio
import time

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.management.base import BaseCommand

from apps.servers.models import Server
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN
from utils.py3xui.async_api import AsyncApi


class Command(BaseCommand):
    help = 'Sync 3x-ui client expiryTime and status label from the balance.'

    def handle(self, *args, **options):
        asyncio.run(self._run())

    async def _run(self) -> None:
        server_id = getattr(settings, 'SPECIAL_MONITOR_SERVER_ID', 1) or 1

        @sync_to_async
        def _load():
            server = Server.objects.get(id=server_id)
            users_qs = TelegramUser.objects.all().annotate_balance()
            rows = []
            for r in UserVPN.objects.select_related('server__tariff').filter(server_id=server.id):
                u = users_qs.filter(id=r.user_id).first()
                if u is None:
                    continue
                rows.append({
                    'vpn_uuid': str(r.vpn_uuid),
                    'balance': float(getattr(u, 'balance', 0) or 0),
                    'price': float(r.server.tariff.price),
                    'enabled': bool(r.enabled),
                })
            return server, rows

        server, rows = await _load()
        api = AsyncApi(server.vpn_url, server.vpn_username, server.vpn_password)
        await api.login()
        mirror = [int(i) for i in (getattr(settings, 'MIRROR_INBOUND_IDS', []) or []) if int(i) != server.inbound_id]
        inbound_ids = [server.inbound_id, *mirror]

        synced = 0
        for row in rows:
            price = row['price']
            if price <= 0:
                continue
            days = int(row['balance'] // price)
            if days > 0:
                status_label = f'осталось {days} дней'
                expiry_ms = int(time.time() * 1000) + days * 86_400_000
                enabled = True
            else:
                status_label = 'подписка окончена'
                expiry_ms = int(time.time() * 1000) - 86_400_000  # already expired
                enabled = False
            for inbound_id in inbound_ids:
                try:
                    await self._sync_one(api, inbound_id, row['vpn_uuid'], expiry_ms, status_label, enabled)
                except Exception:
                    pass
            synced += 1
        self.stdout.write(f'synced_expiry_times={synced}')

    async def _sync_one(self, api: AsyncApi, inbound_id: int, vpn_uuid: str, expiry_ms: int, status_label: str, enabled: bool) -> None:
        inbound = await api.inbound.get_by_id(inbound_id)
        client = next((c for c in inbound.settings.clients if str(c.id) == vpn_uuid), None)
        if client is None:
            return
        client.expiry_time = expiry_ms
        client.email = status_label
        client.enable = enabled
        await api.client.update(vpn_uuid, client)