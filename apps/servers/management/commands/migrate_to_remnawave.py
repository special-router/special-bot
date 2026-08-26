"""Перенос клиентов из ``UserVPN`` в Remnawave с сохранением идентичности.

Переносятся две вещи, и обе — ради того, чтобы уже выданные ссылки продолжили
работать после переключения:

* ``vpn_uuid`` → ``vlessUuid``. С тем же Reality-ключом на ноде ссылка,
  скопированная клиентом месяц назад, поднимается без изменений.
* ``sub_id`` → ``shortUuid``. Панель раздаёт подписку по своему короткому
  идентификатору, наш ``/sub/<sub_id>`` ходит по нашему; равенство избавляет от
  таблицы соответствий.

По умолчанию команда ничего не пишет. Запись включается ``--apply``: создание
клиента в панели — это выдача доступа, и сделать это случайно нельзя.

Идентификаторы не печатаются. ``sub_id`` — это доступ к трафику конкретного
человека, а вывод команды попадает в историю терминала и в тикеты.
"""
from __future__ import annotations

import asyncio

from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand, CommandError

from apps.servers.remnawave import RemnawaveAPI, RemnawaveError, configured
from apps.servers.remnawave_client import (
    RemnawaveVPNClient,
    remnawave_username,
    synchronize_panel_sub_id,
)
from apps.vpn.models import UserVPN


class Command(BaseCommand):
    help = 'Создать в Remnawave клиентов, соответствующих активным UserVPN.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Действительно создавать клиентов. Без него — только отчёт.')
        parser.add_argument(
            '--include-disabled', action='store_true',
            help='Переносить и отключённые записи (создаются со статусом DISABLED).')

    def handle(self, *args, **options):
        if not configured():
            raise CommandError('REMNAWAVE_API_URL и REMNAWAVE_API_TOKEN не заданы.')
        asyncio.run(self._run(apply=options['apply'],
                              include_disabled=options['include_disabled']))

    async def _run(self, *, apply: bool, include_disabled: bool):
        api = RemnawaveAPI()
        records = await sync_to_async(list)(
            UserVPN.objects.select_related('user', 'server').order_by('id'))

        created = existing = skipped = failed = 0
        assigned_sub_ids = 0

        for user_vpn in records:
            if not user_vpn.enabled and not include_disabled:
                skipped += 1
                continue

            try:
                existing_user = await api.get_user_by_username(remnawave_username(user_vpn))
                if existing_user is not None:
                    if apply and not user_vpn.sub_id:
                        await synchronize_panel_sub_id(user_vpn, existing_user)
                        assigned_sub_ids += 1
                    existing += 1
                    continue
                if not apply:
                    created += 1
                    continue
                client = RemnawaveVPNClient(user_vpn.server)
                await client.enable_user(user_vpn, enabled=user_vpn.enabled)
                if user_vpn.sub_id:
                    assigned_sub_ids += 1
                created += 1
            except RemnawaveError:
                failed += 1
                self.stderr.write(f'user_vpn_id={user_vpn.id}: RemnawaveError')

        mode = 'применено' if apply else 'без записи (--apply не задан)'
        self.stdout.write(
            f'{mode}: создано {created}, уже было {existing}, '
            f'пропущено отключённых {skipped}, ошибок {failed}, '
            f'записей без sub_id {assigned_sub_ids}')
        if failed:
            raise CommandError(f'{failed} клиентов не перенесены.')
