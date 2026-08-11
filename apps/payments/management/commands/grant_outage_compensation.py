"""Grant an audited, idempotent 30-day outage compensation campaign."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.db.models.functions import Coalesce

from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.payments.models import CompensationGrant, Transaction
from apps.vpn.models import UserVPN


class Command(BaseCommand):
    help = 'Dry-run or apply one idempotent compensation campaign for VPN key holders.'

    def add_arguments(self, parser):
        parser.add_argument('--campaign', required=True, help='Stable lowercase campaign key, e.g. outage-20260811')
        parser.add_argument('--days', type=int, default=30, help='Compensation days per user key (default: 30)')
        parser.add_argument('--apply', action='store_true', help='Actually create grants and balance transactions')
        parser.add_argument(
            '--include-disabled', action='store_true',
            help='Include historical disabled keys; default is currently enabled keys only.',
        )

    def handle(self, *args, **options):
        campaign = options['campaign']
        days = options['days']
        apply = options['apply']
        include_disabled = options['include_disabled']
        if not campaign.replace('-', '').isalnum() or campaign.lower() != campaign:
            raise CommandError('campaign must be lowercase letters, digits, and hyphens')
        if not 1 <= days <= 90:
            raise CommandError('days must be between 1 and 90')

        rows = self._eligible_amounts(days, include_disabled)
        total = sum(rows.values(), Decimal('0.00'))
        existing = CompensationGrant.objects.filter(campaign=campaign).count()
        self.stdout.write(
            f'campaign={campaign} mode={"apply" if apply else "dry-run"} '
            f'eligible_users={len(rows)} amount_total={total:.2f} '
            f'existing_grants={existing} include_disabled={str(include_disabled).lower()}'
        )
        if not apply:
            return

        created = 0
        skipped = 0
        for user_id, amount in rows.items():
            try:
                with transaction.atomic():
                    grant, made = CompensationGrant.objects.get_or_create(
                        campaign=campaign,
                        user_id=user_id,
                        defaults={'amount': amount},
                    )
                    if not made:
                        if grant.amount != amount:
                            raise CommandError('campaign amount mismatch; use a new campaign key')
                        skipped += 1
                        continue
                    Transaction.objects.create(
                        user_id=user_id,
                        amount=amount,
                        status=TransactionStatusChoices.SUCCESS,
                        source=TransactionSourceChoices.COMPENSATION,
                    )
                    created += 1
            except IntegrityError as exc:
                raise CommandError('compensation grant concurrency failure; retry same campaign') from exc
        self.stdout.write(
            self.style.SUCCESS(
                f'campaign={campaign} created={created} skipped_existing={skipped} amount_total={total:.2f}'
            )
        )

    @staticmethod
    def _eligible_amounts(days: int, include_disabled: bool) -> dict[int, Decimal]:
        keys = UserVPN.objects.select_related('server__tariff')
        if not include_disabled:
            keys = keys.filter(enabled=True)
        daily_amount = ExpressionWrapper(
            F('server__tariff__price') * Decimal(days),
            output_field=DecimalField(max_digits=10, decimal_places=2),
        )
        grouped = (
            keys.values('user_id')
            .annotate(amount=Coalesce(Sum(daily_amount), Decimal('0.00')))
            .order_by('user_id')
        )
        return {row['user_id']: Decimal(row['amount']) for row in grouped if row['amount'] > 0}
