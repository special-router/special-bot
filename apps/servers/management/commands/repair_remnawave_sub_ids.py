"""Reconcile missing Django subscription ids from existing Remnawave users.

The command never prints UUIDs, shortUuid values, usernames, Telegram ids or
subscription URLs. Dry-run is the default. ``--apply`` writes only after every
panel-backed candidate has passed identity and uniqueness validation.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.servers.remnawave import RemnawaveAPI, RemnawaveError, configured
from apps.servers.remnawave_client import panel_subscription_id, remnawave_username
from apps.telegram_bot import broadcast_ops
from apps.telegram_bot.handlers.admin.broadcast import BOT_SERVICE_ACCOUNT_USERNAME
from apps.telegram_bot.models import Broadcast, BroadcastDelivery
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


_NOTIFICATION_TITLE = 'Восстановлена выдача подписки'
_NOTIFICATION_MESSAGE = (
    'Исправили ошибку, из-за которой вместо ссылки подписки мог показываться '
    'одиночный VLESS-ключ. Откройте свою подписку кнопкой ниже и обновите её. '
    'Настройки и оплаченный доступ сохранены.'
)


class Command(BaseCommand):
    help = 'Восстановить пустые UserVPN.sub_id из shortUuid существующих пользователей Remnawave.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Записать проверенные значения в Django.')
        parser.add_argument(
            '--notify', action='store_true',
            help='После --apply уведомить только реально исправленных пользователей.',
        )

    def handle(self, *args, **options):
        if not configured():
            raise CommandError('Remnawave is not configured.')
        if options['notify'] and not options['apply']:
            raise CommandError('Refusing --notify without --apply.')

        candidates = list(
            UserVPN.objects.filter(sub_id='')
            .select_related('user', 'server')
            .order_by('id')
        )
        plans, panel_missing, validation_errors = asyncio.run(self._inspect(candidates))
        validation_errors += self._uniqueness_errors(plans)
        if validation_errors:
            raise CommandError(
                f'Remnawave sub_id validation_failed={validation_errors}; nothing was written.'
            )

        repaired_user_ids: set[int] = set()
        repaired = 0
        notification_id = None
        if options['apply'] and plans:
            with transaction.atomic():
                repaired, repaired_user_ids = self._apply(plans)
                if options['notify'] and repaired_user_ids:
                    notification_id = self._queue_notification(repaired_user_ids)

        mode = 'apply' if options['apply'] else 'dry-run'
        self.stdout.write(
            ' '.join((
                f'mode={mode}',
                f'candidates={len(candidates)}',
                f'panel_found={len(plans)}',
                f'panel_missing={panel_missing}',
                f'repaired={repaired}',
                f'notification_queued={bool(notification_id)}',
            ))
        )

    async def _inspect(self, candidates: list[UserVPN]):
        api = RemnawaveAPI()
        plans: list[tuple[int, int, str]] = []
        panel_missing = 0
        validation_errors = 0
        for user_vpn in candidates:
            try:
                panel_user = await api.get_user_by_username(remnawave_username(user_vpn))
                if panel_user is None:
                    panel_missing += 1
                    continue
                short_uuid = await panel_subscription_id(user_vpn, panel_user)
            except (RemnawaveError, KeyError, TypeError, ValueError):
                validation_errors += 1
                continue
            plans.append((user_vpn.id, user_vpn.user_id, short_uuid))
        return plans, panel_missing, validation_errors

    @staticmethod
    def _uniqueness_errors(plans: list[tuple[int, int, str]]) -> int:
        values = [short_uuid for _vpn_id, _user_id, short_uuid in plans]
        duplicate_plans = sum(count - 1 for count in Counter(values).values() if count > 1)
        existing = UserVPN.objects.filter(sub_id__in=values).exclude(sub_id='').count() if values else 0
        return duplicate_plans + existing

    @staticmethod
    def _apply(plans: list[tuple[int, int, str]]) -> tuple[int, set[int]]:
        repaired_user_ids: set[int] = set()
        repaired = 0
        now = timezone.now()
        locked = {
            row.id: row
            for row in UserVPN.objects.select_for_update().filter(
                id__in=[vpn_id for vpn_id, _user_id, _short_uuid in plans]
            )
        }
        for vpn_id, user_id, short_uuid in plans:
            user_vpn = locked.get(vpn_id)
            if user_vpn is None:
                raise CommandError('A repair candidate disappeared; nothing was written.')
            if user_vpn.sub_id:
                if user_vpn.sub_id != short_uuid:
                    raise CommandError('A repair candidate changed; nothing was written.')
                continue
            if UserVPN.objects.exclude(pk=vpn_id).filter(sub_id=short_uuid).exists():
                raise CommandError('A subscription id became non-unique; nothing was written.')
            user_vpn.sub_id = short_uuid
            user_vpn.updated_at = now
            user_vpn.save(update_fields=['sub_id', 'updated_at'])
            repaired += 1
            repaired_user_ids.add(user_id)
        return repaired, repaired_user_ids

    @staticmethod
    def _queue_notification(user_ids: set[int]) -> int:
        auth_user, _ = get_user_model().objects.get_or_create(
            username=BOT_SERVICE_ACCOUNT_USERNAME,
            defaults={'is_active': False, 'is_staff': False},
        )
        recipients_by_chat: dict[int, TelegramUser] = {}
        for user in TelegramUser.objects.filter(id__in=user_ids).order_by('id'):
            recipients_by_chat.setdefault(user.telegram_id, user)
        if not recipients_by_chat:
            raise CommandError('No notification recipients were found.')

        broadcast = Broadcast.objects.create(
            title=_NOTIFICATION_TITLE,
            message=_NOTIFICATION_MESSAGE,
            audience=Broadcast.AUDIENCE_SUBSCRIPTION_READY,
            include_subscription_button=True,
            status='confirming',
            created_by=auth_user,
            preview_snapshot_id=uuid4(),
            total_users=len(recipients_by_chat),
        )
        BroadcastDelivery.objects.bulk_create([
            BroadcastDelivery(broadcast=broadcast, user=user)
            for user in recipients_by_chat.values()
        ])
        digest = broadcast_ops.confirmation_digest(broadcast)
        result = broadcast_ops.queue_confirmed_broadcast(broadcast.id, digest)
        if not result.ok:
            raise CommandError(f'Notification queue refused: {result.error}.')
        return broadcast.id
