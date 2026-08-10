from __future__ import annotations

import json
from collections import Counter
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.monitoring.models import MonitorState, MonitorTransition
from apps.vpn.models import UserVPN
from ops.origins import validate_origins

LAYER_MAX_AGE = {
    'l0': timedelta(minutes=11),
    'l1': timedelta(minutes=3),
    'l2': timedelta(minutes=11),
    'host': timedelta(minutes=11),
}


def _paging_url_valid(value: str) -> bool:
    parsed = urlsplit(value)
    return bool(parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password)


class Command(BaseCommand):
    help = 'Read-only aggregate readiness report for scale and legacy retirement.'

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true', dest='as_json')
        parser.add_argument('--origins-file', type=Path)

    def handle(self, *args, **options):
        records = list(
            UserVPN.objects.select_related('server', 'server__tariff').annotate(
                entitlement_balance=Coalesce(
                    Sum('user__transactions__amount'),
                    Value(0),
                    output_field=DecimalField(max_digits=10, decimal_places=2),
                )
            )
        )
        entitled_records = [row for row in records if row.entitlement_balance >= row.server.tariff.price]
        entitled_sub_ids = [row.sub_id for row in entitled_records if row.sub_id]
        duplicate_sub_ids = sum(count - 1 for count in Counter(row.sub_id for row in records if row.sub_id).values() if count > 1)
        entitled_missing_sub_id = len(entitled_records) - len(entitled_sub_ids)

        now = timezone.now()
        states = {row.layer: row for row in MonitorState.objects.all()}
        healthy_layers = sorted(
            layer
            for layer, max_age in LAYER_MAX_AGE.items()
            if (state := states.get(layer))
            and state.last_ok
            and not state.alert
            and now - state.checked_at <= max_age
        )
        monitoring_complete = set(LAYER_MAX_AGE).issubset(healthy_layers)

        paging_configured = bool(
            settings.SPECIAL_MONITOR_PAGING_ENABLED
            and settings.SPECIAL_MONITOR_PAGING_OWNER.strip()
            and _paging_url_valid(settings.SPECIAL_MONITOR_PAGING_WEBHOOK_URL.strip())
        )
        latest_delivery = (
            MonitorTransition.objects.filter(notification_delivered=True)
            .exclude(notification_destination_owner='')
            .order_by('-notification_attempted_at')
            .first()
        )
        paging_delivery_verified = bool(
            latest_delivery
            and latest_delivery.notification_destination_owner == settings.SPECIAL_MONITOR_PAGING_OWNER.strip()
            and latest_delivery.notification_attempted_at
            and now - latest_delivery.notification_attempted_at <= timedelta(days=30)
        )

        origins_enabled = 0
        independent_origins_configured = False
        if options['origins_file']:
            rows = json.loads(options['origins_file'].read_text(encoding='utf-8'))
            origin_result = validate_origins(rows)
            origins_enabled = int(origin_result['enabled'])
            independent_origins_configured = bool(origin_result['independent_origins_configured'])

        report = {
            'records': len(records),
            'entitled': len(entitled_records),
            'entitled_missing_sub_id': entitled_missing_sub_id,
            'duplicate_sub_ids': duplicate_sub_ids,
            'subscription_coverage_complete': entitled_missing_sub_id == 0 and duplicate_sub_ids == 0,
            'healthy_monitor_layers': healthy_layers,
            'monitoring_complete': monitoring_complete,
            'paging_configured': paging_configured,
            'paging_delivery_verified': paging_delivery_verified,
            'enabled_origins': origins_enabled,
            'independent_origins_configured': independent_origins_configured,
            # Healthy independent origins require real protected probes; metadata is insufficient.
            'redundancy_ready': False,
            # Compatibility ownership is deliberately not inferred from control-plane extras.
            'compatibility_ownership': 'external_private_registry_required',
            'legacy_retirement_ready': False,
        }
        if options['as_json']:
            self.stdout.write(json.dumps(report, sort_keys=True))
            return
        rendered = []
        for key, value in report.items():
            if isinstance(value, list):
                value = ','.join(value) or 'none'
            rendered.append(f'{key}={str(value).lower()}')
        self.stdout.write(' '.join(rendered))
