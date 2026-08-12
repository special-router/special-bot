"""Восстановить журнал событий из существующих строк ``Transaction``."""
from __future__ import annotations

import datetime

from django.core.management.base import BaseCommand, CommandError

from apps.analytics.backfill import backfill_billing_gaps, backfill_money_events


class Command(BaseCommand):
    help = 'Idempotently derive analytics events from existing transactions. Safe to re-run.'

    def add_arguments(self, parser):
        parser.add_argument('--since', help='Первая дата (YYYY-MM-DD), включительно')
        parser.add_argument('--until', help='Последняя дата (YYYY-MM-DD), включительно')
        parser.add_argument(
            '--refresh',
            action='store_true',
            help='Пересчитать классификацию уже записанных событий после изменения таксономии',
        )
        parser.add_argument(
            '--skip-billing-gaps',
            action='store_true',
            help='Не выводить отвалы и возвраты аккаунтов из ряда дат списаний',
        )

    def handle(self, *args, **options):
        since = _parse_date(options['since'], '--since')
        until = _parse_date(options['until'], '--until')
        if since and until and since > until:
            raise CommandError('--since must not be later than --until')

        result = backfill_money_events(since=since, until=until, refresh=options['refresh'])
        if not options['skip_billing_gaps']:
            backfill_billing_gaps(since=since, until=until, result=result)

        self.stdout.write(self.style.SUCCESS(f'backfill_money_events {result.as_line()}'))


def _parse_date(value: str | None, flag: str) -> datetime.date | None:
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise CommandError(f'{flag} must be YYYY-MM-DD') from exc
