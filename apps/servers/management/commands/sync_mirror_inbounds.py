"""Разово догнать зеркальные inbound-ы до состава основного.

``MIRROR_INBOUND_IDS`` наполняется сам, но только при следующей записи по
клиенту: выдача, отключение, включение. Клиент, которого после появления
зеркала никто не трогал, в нём так и не появится — и получит в подписке строку
в транспорт, где его UUID неизвестен. Эта команда закрывает разрыв один раз,
после чего обычный путь снова достаточен.

Читает состав inbound-а перед записью и добавляет только отсутствующих, поэтому
повторный запуск ничего не меняет. Без ``--apply`` только считает.
"""
import asyncio

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from py3xui import Client

from apps.servers.models import Server
from apps.vpn.models import UserVPN
from utils.py3xui.async_api import AsyncApi


class Command(BaseCommand):
    help = 'Add every enabled subscription UUID missing from the configured mirror inbounds.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Write the missing clients; omitted means a read-only count.')
        parser.add_argument('--server-id', type=int, help='Limit to one configured Server record.')
        parser.add_argument('--inbound-id', type=int, action='append',
                            help='Limit to one mirror inbound; repeatable. Defaults to MIRROR_INBOUND_IDS.')

    def handle(self, *args, **options):
        configured = [int(value) for value in (getattr(settings, 'MIRROR_INBOUND_IDS', []) or [])]
        if not configured:
            raise CommandError('MIRROR_INBOUND_IDS is empty: nothing to mirror into.')

        targets = options['inbound_id'] or configured
        unknown = [value for value in targets if value not in configured]
        if unknown:
            # Иначе команда стала бы способом записать клиентов в произвольный
            # inbound по номеру, а состав inbound-а — это доступ.
            raise CommandError(f'Refusing: inbound(s) {unknown} are not in MIRROR_INBOUND_IDS.')

        servers = Server.objects.order_by('id')
        if options['server_id']:
            servers = servers.filter(id=options['server_id'])
        servers = list(servers)
        if not servers:
            raise CommandError('No matching configured servers.')

        for server in servers:
            uuids = [
                str(value) for value in UserVPN.objects
                .filter(server=server, enabled=True)
                .order_by('created_at')
                .values_list('vpn_uuid', flat=True)
            ]
            if not uuids:
                self.stdout.write(f'server {server.id}: no enabled subscriptions')
                continue
            asyncio.run(self._sync_server(server, targets, uuids, apply=options['apply']))

    async def _sync_server(self, server, targets, uuids, *, apply: bool):
        api = AsyncApi(server.vpn_url, server.vpn_username, server.vpn_password)
        await api.login()
        for inbound_id in targets:
            if inbound_id == server.inbound_id:
                continue
            inbound = await api.inbound.get_by_id(inbound_id)
            present = {str(client.id) for client in (inbound.settings.clients or [])}
            missing = [value for value in uuids if value not in present]
            verb = 'adding' if apply else 'would add'
            self.stdout.write(
                f'server {server.id} inbound {inbound_id}: {len(present)} present, {verb} {len(missing)}')
            if not apply or not missing:
                continue
            for value in missing:
                # По одному: панель отвечает на пакетную вставку целиком, и одна
                # отвергнутая запись унесла бы с собой остальные.
                await api.client.add(inbound_id, [Client(
                    id=value,
                    email='',
                    enable=True,
                    limit_ip=settings.LIMIT_IP,
                )])
            self.stdout.write(self.style.SUCCESS(
                f'server {server.id} inbound {inbound_id}: added {len(missing)}'))
