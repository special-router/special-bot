"""Ключевые денежные метрики периода: текстом в терминал или JSON под разбор."""
from __future__ import annotations

import datetime
import json
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.analytics.reporting import build_report, format_report


class Command(BaseCommand):
    help = 'Print cash in, recognised revenue, credit granted, payouts, ARPU, churn and cohorts for a period.'

    def add_arguments(self, parser):
        parser.add_argument('--since', help='Первая дата периода (YYYY-MM-DD). По умолчанию 30 дней назад')
        parser.add_argument('--until', help='Последняя дата периода (YYYY-MM-DD). По умолчанию сегодня')
        parser.add_argument('--cohorts', type=int, default=12, help='Сколько последних когорт печатать (0 — все)')
        parser.add_argument('--json', action='store_true', help='Выдать машиночитаемый JSON вместо текста')

    def handle(self, *args, **options):
        today = timezone.now().astimezone(datetime.timezone.utc).date()
        end = _parse_date(options['until'], '--until') or today
        start = _parse_date(options['since'], '--since') or end - datetime.timedelta(days=30)
        if start > end:
            raise CommandError('--since must not be later than --until')
        if options['cohorts'] < 0:
            raise CommandError('--cohorts must not be negative')

        report = build_report(start, end, cohorts=options['cohorts'])
        if options['json']:
            self.stdout.write(json.dumps(report, default=_json_default, ensure_ascii=False, sort_keys=True))
            return
        self.stdout.write(format_report(report))


def _json_default(value):
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f'unserialisable value of type {type(value).__name__}')


def _parse_date(value: str | None, flag: str) -> datetime.date | None:
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise CommandError(f'{flag} must be YYYY-MM-DD') from exc
