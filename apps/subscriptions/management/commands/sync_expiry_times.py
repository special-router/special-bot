"""Sync 3x-ui client expiryTime from balance so subscription clients display the
remaining days in happ and other subscription-aware clients.

This is read-only with respect to billing: it never creates transactions, never
enables a disabled client, and only updates ``expiryTime`` (and mirrors it to
``MIRROR_INBOUND_IDS``). Disabling on low balance is owned by ``update_user_vpn``.
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
    help = 'Sync 3x-ui client expiryTime from the remaining balance days.'

    def handle(self, *args, **options):
        asyncio.run(self._run())

    async def _run(self) -> None:
        server_id = getattr(settings, 'SPECIAL_MONITOR_SERVER_ID', 1) or 1
        server = await Server.objects.aget(id=server_id)
        api = AsyncApi(server.vpn_url, server.vpn_username, server.vpn_password)
        await api.login()
        mirror = [int(i) for i in (getattr(settings, 'MIRROR_INBOUND_IDS', []) or []) if int(i) != server.inbound_id]
        inbound_ids = [server.inbound_id, *mirror]

        @sync_to_async
        def _load():
            return list(
                UserVPN.objects.with_related_user(TelegramUser.objects.all().annotate_balance())
                .with_related_server()
                .filter_by_enabled(True)
                .filter(server_id=server.id)
            )

        user_vpns = await _load()
        synced = 0
        for user_vpn in user_vpns:
            price = user_vpn.server.tariff.price
            if price <= 0:
                continue
            days = int(user_vpn.user.balance // price)
            expiry_ms = int(time.time() * 1000) + days * 86_400_000
            for inbound_id in inbound_ids:
                try:
                    await self._sync_one(api, inbound_id, str(user_vpn.vpn_uuid), expiry_ms)
                except Exception:
                    pass
            synced += 1
        self.stdout.write(f'synced_expiry_times={synced}')

    async def _sync_one(self, api: AsyncApi, inbound_id: int, vpn_uuid: str, expiry_ms: int) -> None:
        inbound = await api.inbound.get_by_id(inbound_id)
        client = next((c for c in inbound.settings.clients if str(c.id) == vpn_uuid), None)
        if client is None:
            return
        client.expiry_time = expiry_ms
        await api.client.update(vpn_uuid, client)