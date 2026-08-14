"""Свести аккаунты с несколькими подписками к одной, не меняя их счёт.

Подписок на аккаунт стало ровно одна, а места под устройства покупаются
отдельно. Девять аккаунтов, заведших по две-три подписки ради устройств,
нужно перевести в новую модель так, чтобы суточная плата у них не изменилась.

Арифметика: сегодня n подписок стоят n тарифов в сутки. Завтра одна подписка с
лимитом L стоит тариф × (1 + max(0, L − бесплатные)). Приравнивая при двух
бесплатных местах, получаем **L = n + 1**. Мест выходит меньше, чем сумма
старых (4 вместо 6 при трёх подписках), и это осознанный выбор владельца от
2026-08-14: ни один из этих аккаунтов не занял больше одного устройства, а
буквальное сложение мест подняло бы им плату в полтора раза.

Лишние подписки удаляются вместе с их клиентами в панели: оставить их
включёнными значило бы и дальше списывать за них деньги, а выключенными — дать
бесплатный доступ по ссылке, которая продолжает работать.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict

from django.core.management.base import BaseCommand

from apps.subscriptions.devices import set_device_limit
from apps.subscriptions.pricing import free_device_slots
from apps.vpn.models import UserVPN
from apps.vpn.services.remove_vpn_user_from_server import remove_vpn_user_from_server


class Command(BaseCommand):
    help = 'Collapse every account with more than one subscription into a single one.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Actually collapse. Without it nothing is written or deleted.')

    def handle(self, *args, **options):
        by_user: dict[int, list[UserVPN]] = defaultdict(list)
        for user_vpn in UserVPN.objects.with_related_server().order_by('created_at', 'id'):
            by_user[user_vpn.user_id].append(user_vpn)

        crowded = {user_id: items for user_id, items in by_user.items() if len(items) > 1}
        if not crowded:
            self.stdout.write('every account already has at most one subscription')
            return

        free = free_device_slots()
        collapsed = removed = 0
        for user_id, items in sorted(crowded.items()):
            keeper, extras = items[0], items[1:]
            # Место под каждую сверхштатную подписку сверх бесплатных: цена в
            # сутки остаётся ровно той, что аккаунт платит сегодня.
            limit = free + len(extras)
            self.stdout.write(
                f'user {user_id}: keep {keeper.id}, drop {[item.id for item in extras]}, '
                f'device_limit {limit}')
            if not options['apply']:
                continue

            set_device_limit(keeper, limit)
            for extra in extras:
                # Панель и база чистятся одним вызовом; сбой на одной подписке не
                # должен оставить остальные наполовину сведёнными.
                try:
                    asyncio.run(remove_vpn_user_from_server(extra))
                except Exception as error:
                    self.stdout.write(f'  FAILED to remove {extra.id}: {type(error).__name__}')
                    continue
                removed += 1
            collapsed += 1

        if not options['apply']:
            self.stdout.write(f'dry run: {len(crowded)} accounts would be collapsed; pass --apply')
            return
        self.stdout.write(f'collapsed {collapsed} accounts, removed {removed} extra subscriptions')
