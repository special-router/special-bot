"""Sync 3x-ui client expiryTime and status label so subscription clients (happ)
display how many days remain or that the subscription has ended.

Working inbounds (primary + ``MIRROR_INBOUND_IDS``) receive ``expiryTime`` only
and keep an empty ``email`` so the subscription remark stays clean
(e.g. ``🇳🇱 NL Direct``).

The optional ``STATUS_INBOUND_ID`` inbound additionally carries the per-client
status in its ``email`` field, producing a remark like
``📊 Подписка-осталось 28 дней``. That inbound points at a non-working dest so a
client cannot actually tunnel through it; it is an info-only entry in happ.

* balance covers at least one day  -> ``осталось N дней`` and expiryTime = now + N*d
* balance cannot cover one day        -> ``подписка окончена`` and the client is disabled

Daily billing and the authoritative disable are owned by ``update_user_vpn``;
this command only mirrors state to 3x-ui.
"""
from __future__ import annotations

import asyncio
import logging
import time

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.servers.internal_membership import InternalMembershipSyncError, sync_internal_memberships
from apps.servers.models import Server
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN
from utils.py3xui.async_api import AsyncApi


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Sync 3x-ui client expiryTime and status label from the balance.'

    def handle(self, *args, **options):
        asyncio.run(self._run())

    async def _run(self) -> None:
        server_id = getattr(settings, 'SPECIAL_MONITOR_SERVER_ID', 1) or 1
        status_inbound_id = int(getattr(settings, 'STATUS_INBOUND_ID', 0) or 0)

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
                    'user_vpn_id': r.id,
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
        working_ids = [server.inbound_id, *mirror]

        synced = 0
        errors: list[str] = []
        working_failures: dict[int, int] = {}
        status_failures = 0
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
                expiry_ms = int(time.time() * 1000) - 86_400_000
                enabled = False

            # Working inbounds: expiryTime + enable only, keep email empty.
            for inbound_id in working_ids:
                try:
                    await self._sync_one(api, inbound_id, row['vpn_uuid'], expiry_ms, '', enabled)
                except Exception as error:
                    # Never hide a misconfigured inbound behind a silent pass.
                    working_failures[inbound_id] = working_failures.get(inbound_id, 0) + 1
                    logger.warning('Expiry sync failed: inbound=%s reason=%s', inbound_id, type(error).__name__)
            # The canary is intentionally outside MIRROR_INBOUND_IDS.  It can
            # update only existing exact UUID memberships and validates every
            # configured retained target before mutating any of them.
            try:
                await sync_internal_memberships(
                    api, type('UserVPNRef', (), {'id': row['user_vpn_id'], 'vpn_uuid': row['vpn_uuid']})(),
                    enabled=enabled, expiry_time=expiry_ms)
            except InternalMembershipSyncError:
                errors.append('internal_membership_sync')
            # Status inbound: additionally write the status label into email.
            if status_inbound_id:
                try:
                    await self._sync_one(api, status_inbound_id, row['vpn_uuid'], expiry_ms, status_label, enabled)
                except Exception as error:
                    status_failures += 1
                    logger.warning(
                        'Status label sync failed: inbound=%s reason=%s',
                        status_inbound_id, type(error).__name__)
            synced += 1
        self.stdout.write(f'synced_expiry_times={synced}')
        # Surface configuration drift: an inbound that fails for every row is
        # almost always a stale configured id rather than a transient error.
        for inbound_id, failures in sorted(working_failures.items()):
            self.stdout.write(f'inbound_sync_failures inbound={inbound_id} rows={failures}')
        if status_failures:
            self.stdout.write(
                f'status_inbound_sync_failures inbound={status_inbound_id} rows={status_failures}')
        if errors:
            # Do not include client or panel details in scheduler-visible output.
            raise CommandError(f'internal_membership_sync_errors={len(errors)}')

    async def _sync_one(self, api: AsyncApi, inbound_id: int, vpn_uuid: str, expiry_ms: int, status_label: str, enabled: bool) -> None:
        inbound = await api.inbound.get_by_id(inbound_id)
        client = next((c for c in inbound.settings.clients if str(c.id) == vpn_uuid), None)
        if client is None:
            return
        client.expiry_time = expiry_ms
        client.email = status_label
        client.enable = enabled
        client.inbound_id = inbound_id
        await api.client.update(vpn_uuid, client)