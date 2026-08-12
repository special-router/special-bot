"""Метрики периода поверх журнала событий. Баланс здесь не пересчитывается.

Все суммы берутся из ``MoneyEvent`` со статусом ``SUCCESS``: это экономический
взгляд, а не бухгалтерский остаток. Строки других статусов на баланс влияют
(``annotate_balance`` не фильтрует статус), поэтому их количество печатается
отдельной строкой — расхождение отчёта с балансом должно быть видно, а не
скрыто.
"""
from __future__ import annotations

import datetime
from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, Min, Q, Sum

from apps.analytics.choices import EconomicClassChoices, FunnelStepChoices, MoneyEventKindChoices
from apps.analytics.models import FunnelEvent, MoneyEvent
from apps.payments.choices import TransactionStatusChoices
from apps.users.models import TelegramUser


ZERO = Decimal('0.00')

FUNNEL_ORDER = (
    FunnelStepChoices.BALANCE_SCREEN_SHOWN,
    FunnelStepChoices.PROMO_CLAIMED,
    FunnelStepChoices.TOPUP_PLAN_CHOSEN,
    FunnelStepChoices.INVOICE_SENT,
    FunnelStepChoices.PRE_CHECKOUT_APPROVED,
    FunnelStepChoices.PAYMENT_COMPLETED,
)

CREDIT_KINDS = (
    MoneyEventKindChoices.SIGNUP_PROMO,
    MoneyEventKindChoices.TOPUP,
    MoneyEventKindChoices.OUTAGE_COMPENSATION,
    MoneyEventKindChoices.MANUAL_CREDIT,
)


def _money(value) -> Decimal:
    return Decimal(value or 0).quantize(Decimal('0.01'))


def _ratio(part: Decimal, whole: Decimal) -> float:
    return float(part / whole * 100) if whole else 0.0


def _per_user(total: Decimal, users: int) -> Decimal:
    return _money(total / users) if users else ZERO


def build_report(start: datetime.date, end: datetime.date, *, cohorts: int = 12) -> dict:
    """Собрать все метрики периода в один словарь.

    Когорты и конверсия промо считаются за всё время до ``end``, а не за период:
    вопрос «окупается ли скидка» бессмыслен внутри одного месяца.
    """
    success = MoneyEvent.objects.filter(status=TransactionStatusChoices.SUCCESS)
    period = success.filter(effective_date__gte=start, effective_date__lte=end)
    lifetime = success.filter(effective_date__lte=end)

    by_kind = {
        row['kind']: row
        for row in period.values('kind').annotate(
            cash=Sum('cash_amount'),
            revenue=Sum('revenue_amount'),
            credit=Sum('credit_amount'),
            payout=Sum('payout_amount'),
            rows=Count('id'),
        )
    }
    lifetime_by_user = {
        row['user_id']: row
        for row in lifetime.values('user_id').annotate(
            cash=Sum('cash_amount'),
            revenue=Sum('revenue_amount'),
            credit=Sum('credit_amount'),
            payout=Sum('payout_amount'),
            days=Count('id', filter=Q(kind=MoneyEventKindChoices.DAILY_CHARGE)),
        )
    }

    cash = _cash_section(period, by_kind)
    revenue = _revenue_section(period, by_kind, lifetime_by_user)
    credit = _credit_section(period, by_kind)
    customers = _customers_section(period, lifetime, start, end)

    return {
        'period': {'start': start.isoformat(), 'end': end.isoformat()},
        'cash': cash,
        'revenue': revenue,
        'credit_granted': credit,
        'payouts': _payout_section(by_kind),
        'adjustments': _adjustment_section(period, start, end),
        'customers': customers,
        'promo': _promo_section(period, lifetime, lifetime_by_user, by_kind),
        'referral': _referral_section(period, lifetime, by_kind),
        'churn': _churn_section(start, end),
        'funnel': _funnel_section(start, end),
        'cohorts': _cohort_section(lifetime_by_user, end, cohorts),
    }


def _cash_section(period, by_kind: dict) -> dict:
    topup = by_kind.get(MoneyEventKindChoices.TOPUP, {})
    manual = by_kind.get(MoneyEventKindChoices.MANUAL_CREDIT, {})
    basis_rows = {
        row['cash_basis']: row['rows']
        for row in period.filter(cash_amount__gt=0).values('cash_basis').annotate(rows=Count('id'))
    }
    return {
        'total': _money(period.aggregate(total=Sum('cash_amount'))['total']),
        'provider_topup': _money(topup.get('cash')),
        'provider_topup_rows': topup.get('rows', 0),
        # Начисления руками: возможно, деньги вне провайдера. Не складываются с
        # поступлениями, потому что история не говорит, платил ли кто-то.
        'manual_credit_unknown_cash': _money(manual.get('credit')),
        'manual_credit_rows': manual.get('rows', 0),
        'basis_rows': basis_rows,
        'rows_outside_bonus_ladder': period.filter(
            kind=MoneyEventKindChoices.TOPUP, cash_basis='UNKNOWN'
        ).count(),
    }


def _revenue_section(period, by_kind: dict, lifetime_by_user: dict) -> dict:
    daily = by_kind.get(MoneyEventKindChoices.DAILY_CHARGE, {})
    purchase = by_kind.get(MoneyEventKindChoices.SUBSCRIPTION_PURCHASE, {})
    total = _money(period.aggregate(total=Sum('revenue_amount'))['total'])

    # Чем оплачена выручка периода. Транзакция не помечает, из каких денег списан
    # рубль, поэтому доля берётся из накопленной структуры пополнений аккаунта:
    # это оценка, а не факт, и подписана в отчёте как оценка.
    funded_cash = ZERO
    for row in period.values('user_id').annotate(revenue=Sum('revenue_amount')):
        user_revenue = _money(row['revenue'])
        if not user_revenue:
            continue
        totals = lifetime_by_user.get(row['user_id'], {})
        inflow_cash = _money(totals.get('cash'))
        inflow_free = _money(totals.get('credit')) + _money(totals.get('payout'))
        inflow = inflow_cash + inflow_free
        if inflow:
            funded_cash += user_revenue * inflow_cash / inflow
    funded_cash = _money(funded_cash)

    return {
        'total': total,
        'daily_charge': _money(daily.get('revenue')),
        'subscription_days': daily.get('rows', 0),
        'subscription_purchase': _money(purchase.get('revenue')),
        'subscription_purchase_rows': purchase.get('rows', 0),
        'funded_by_cash': funded_cash,
        'funded_by_credit': _money(total - funded_cash),
        'funded_by_cash_percent': round(_ratio(funded_cash, total), 1),
    }


def _credit_section(period, by_kind: dict) -> dict:
    detail = {}
    for kind in CREDIT_KINDS:
        row = by_kind.get(kind, {})
        detail[kind] = {'amount': _money(row.get('credit')), 'rows': row.get('rows', 0)}
    return {
        'total': _money(period.aggregate(total=Sum('credit_amount'))['total']),
        'by_kind': detail,
    }


def _payout_section(by_kind: dict) -> dict:
    row = by_kind.get(MoneyEventKindChoices.REFERRAL_PAYOUT, {})
    return {'referral': _money(row.get('payout')), 'rows': row.get('rows', 0)}


def _adjustment_section(period, start: datetime.date, end: datetime.date) -> dict:
    adjustments = period.filter(economic_class=EconomicClassChoices.ADJUSTMENT)
    unknown = period.filter(economic_class=EconomicClassChoices.UNKNOWN)
    return {
        'balance_delta': _money(adjustments.aggregate(total=Sum('balance_delta'))['total']),
        'rows': adjustments.count(),
        'unclassified_rows': unknown.count(),
        'unclassified_balance_delta': _money(unknown.aggregate(total=Sum('balance_delta'))['total']),
        'non_success_rows': MoneyEvent.objects.filter(effective_date__gte=start, effective_date__lte=end)
        .exclude(status=TransactionStatusChoices.SUCCESS)
        .count(),
    }


def _customers_section(period, lifetime, start: datetime.date, end: datetime.date) -> dict:
    active = period.filter(revenue_amount__gt=0).values('user_id').distinct().count()
    paying = period.filter(cash_amount__gt=0).values('user_id').distinct().count()
    first_cash = lifetime.filter(cash_amount__gt=0).values('user_id').annotate(first=Min('effective_date'))
    first_payers = sum(1 for row in first_cash if start <= row['first'] <= end)
    revenue = _money(period.aggregate(total=Sum('revenue_amount'))['total'])
    cash = _money(period.aggregate(total=Sum('cash_amount'))['total'])
    return {
        'active_users': active,
        'paying_users': paying,
        'new_users': TelegramUser.objects.filter(
            created_at__date__gte=start, created_at__date__lte=end
        ).count(),
        'first_time_payers': first_payers,
        'arpu_revenue': _per_user(revenue, active),
        'arpu_cash': _per_user(cash, active),
    }


def _promo_section(period, lifetime, lifetime_by_user: dict, by_kind: dict) -> dict:
    recipients = set(
        lifetime.filter(kind=MoneyEventKindChoices.SIGNUP_PROMO).values_list('user_id', flat=True)
    )
    converted = {user_id for user_id in recipients if _money(lifetime_by_user.get(user_id, {}).get('cash')) > 0}
    cash_from_converted = sum(
        (_money(lifetime_by_user.get(user_id, {}).get('cash')) for user_id in converted), ZERO
    )
    promo = by_kind.get(MoneyEventKindChoices.SIGNUP_PROMO, {})
    lifetime_cost = _money(
        lifetime.filter(kind=MoneyEventKindChoices.SIGNUP_PROMO).aggregate(total=Sum('credit_amount'))['total']
    )
    return {
        'granted_in_period': _money(promo.get('credit')),
        'granted_rows_in_period': promo.get('rows', 0),
        'recipients_lifetime': len(recipients),
        'converted_lifetime': len(converted),
        'conversion_percent': round(len(converted) / len(recipients) * 100, 1) if recipients else 0.0,
        'cost_lifetime': lifetime_cost,
        'cash_from_converted_lifetime': _money(cash_from_converted),
        'margin_lifetime': _money(cash_from_converted - lifetime_cost),
    }


def _referral_section(period, lifetime, by_kind: dict) -> dict:
    referred_ids = set(
        TelegramUser.objects.filter(referral_user__isnull=False).values_list('id', flat=True)
    )
    payout_period = _money(by_kind.get(MoneyEventKindChoices.REFERRAL_PAYOUT, {}).get('payout'))
    payout_lifetime = _money(
        lifetime.filter(kind=MoneyEventKindChoices.REFERRAL_PAYOUT).aggregate(total=Sum('payout_amount'))['total']
    )
    cash_period = _money(
        period.filter(user_id__in=referred_ids).aggregate(total=Sum('cash_amount'))['total']
    )
    cash_lifetime = _money(
        lifetime.filter(user_id__in=referred_ids).aggregate(total=Sum('cash_amount'))['total']
    )
    return {
        'referred_users': len(referred_ids),
        'payout_period': payout_period,
        'cash_from_referred_period': cash_period,
        'margin_period': _money(cash_period - payout_period),
        'payout_lifetime': payout_lifetime,
        'cash_from_referred_lifetime': cash_lifetime,
        'margin_lifetime': _money(cash_lifetime - payout_lifetime),
    }


def _churn_section(start: datetime.date, end: datetime.date) -> dict:
    events = FunnelEvent.objects.filter(effective_date__gte=start, effective_date__lte=end)
    disabled = events.filter(step=FunnelStepChoices.SUBSCRIPTION_DISABLED_NO_FUNDS)
    lapsed = events.filter(step=FunnelStepChoices.ACCOUNT_BILLING_LAPSED)
    resumed = events.filter(step=FunnelStepChoices.ACCOUNT_BILLING_RESUMED)
    return {
        'subscriptions_disabled_no_funds': disabled.count(),
        'accounts_disabled_no_funds': disabled.values('user_id').distinct().count(),
        'accounts_lapsed': lapsed.values('user_id').distinct().count(),
        'account_lapse_events': lapsed.count(),
        'accounts_resumed': resumed.values('user_id').distinct().count(),
        'account_resume_events': resumed.count(),
    }


def _funnel_section(start: datetime.date, end: datetime.date) -> dict:
    counts = {
        row['step']: row['rows']
        for row in FunnelEvent.objects.filter(effective_date__gte=start, effective_date__lte=end)
        .values('step')
        .annotate(rows=Count('id'))
    }
    return {step: counts.get(step, 0) for step in FUNNEL_ORDER}


def _cohort_section(lifetime_by_user: dict, end: datetime.date, cohorts: int) -> list[dict]:
    """Пожизненные суммы по месяцу регистрации аккаунта, до ``end`` включительно."""
    months: dict[str, dict] = defaultdict(
        lambda: {'users': 0, 'cash': ZERO, 'revenue': ZERO, 'subscription_days': 0}
    )
    signups = TelegramUser.objects.filter(created_at__date__lte=end).values_list('id', 'created_at')
    for user_id, created_at in signups.iterator(chunk_size=2000):
        month = created_at.astimezone(datetime.timezone.utc).strftime('%Y-%m')
        bucket = months[month]
        bucket['users'] += 1
        totals = lifetime_by_user.get(user_id, {})
        bucket['cash'] += _money(totals.get('cash'))
        bucket['revenue'] += _money(totals.get('revenue'))
        bucket['subscription_days'] += totals.get('days', 0)

    ordered = sorted(months.items())[-cohorts:] if cohorts else sorted(months.items())
    return [
        {
            'month': month,
            'users': bucket['users'],
            'cash': _money(bucket['cash']),
            'revenue': _money(bucket['revenue']),
            'cash_per_user': _per_user(bucket['cash'], bucket['users']),
            'revenue_per_user': _per_user(bucket['revenue'], bucket['users']),
            'subscription_days_per_user': round(bucket['subscription_days'] / bucket['users'], 1)
            if bucket['users']
            else 0.0,
        }
        for month, bucket in ordered
    ]


def format_report(data: dict) -> str:
    """Плоский текст под чтение в терминале: раздел, отступ, ключ=значение."""
    period = data['period']
    cash = data['cash']
    revenue = data['revenue']
    credit = data['credit_granted']
    customers = data['customers']
    promo = data['promo']
    referral = data['referral']
    churn = data['churn']

    lines = [f"money_report period={period['start']}..{period['end']}", '', 'CASH IN']
    lines += [
        f"  received_total={cash['total']}",
        f"  provider_topup={cash['provider_topup']} rows={cash['provider_topup_rows']}",
        f"  manual_credit_unknown_cash={cash['manual_credit_unknown_cash']} rows={cash['manual_credit_rows']}"
        '  # начислено руками: подарок или деньги мимо провайдера — история не различает',
        f"  rows_outside_bonus_ladder={cash['rows_outside_bonus_ladder']}",
        f"  basis_rows={_format_counts(cash['basis_rows'])}",
        '',
        'REVENUE RECOGNISED',
        f"  total={revenue['total']}",
        f"  daily_charge={revenue['daily_charge']} subscription_days={revenue['subscription_days']}",
        f"  subscription_purchase={revenue['subscription_purchase']} rows={revenue['subscription_purchase_rows']}",
        f"  funded_by_cash={revenue['funded_by_cash']} ({revenue['funded_by_cash_percent']}%)"
        '  # оценка по структуре пополнений аккаунта',
        f"  funded_by_credit={revenue['funded_by_credit']}",
        '',
        'CREDIT GRANTED (marketing cost)',
        f"  total={credit['total']}",
    ]
    for kind, row in credit['by_kind'].items():
        lines.append(f"  {kind.lower()}={row['amount']} rows={row['rows']}")
    lines += [
        '',
        'PAYOUTS',
        f"  referral={data['payouts']['referral']} rows={data['payouts']['rows']}",
        '',
        'ADJUSTMENTS',
        f"  balance_delta={data['adjustments']['balance_delta']} rows={data['adjustments']['rows']}",
        f"  unclassified_rows={data['adjustments']['unclassified_rows']} "
        f"balance_delta={data['adjustments']['unclassified_balance_delta']}",
        f"  non_success_rows={data['adjustments']['non_success_rows']}"
        '  # влияют на баланс, но не на экономику',
        '',
        'CUSTOMERS',
        f"  active_users={customers['active_users']} paying_users={customers['paying_users']} "
        f"new_users={customers['new_users']} first_time_payers={customers['first_time_payers']}",
        f"  arpu_revenue={customers['arpu_revenue']} arpu_cash={customers['arpu_cash']}",
        '',
        'PROMO',
        f"  granted_in_period={promo['granted_in_period']} rows={promo['granted_rows_in_period']}",
        f"  recipients_lifetime={promo['recipients_lifetime']} converted_lifetime={promo['converted_lifetime']} "
        f"conversion={promo['conversion_percent']}%",
        f"  cost_lifetime={promo['cost_lifetime']} "
        f"cash_from_converted_lifetime={promo['cash_from_converted_lifetime']} "
        f"margin_lifetime={promo['margin_lifetime']}",
        '',
        'REFERRAL PROGRAMME',
        f"  referred_users={referral['referred_users']}",
        f"  payout_period={referral['payout_period']} "
        f"cash_from_referred_period={referral['cash_from_referred_period']} "
        f"margin_period={referral['margin_period']}",
        f"  payout_lifetime={referral['payout_lifetime']} "
        f"cash_from_referred_lifetime={referral['cash_from_referred_lifetime']} "
        f"margin_lifetime={referral['margin_lifetime']}",
        '',
        'CHURN',
        f"  subscriptions_disabled_no_funds={churn['subscriptions_disabled_no_funds']} "
        f"accounts={churn['accounts_disabled_no_funds']}",
        f"  accounts_lapsed={churn['accounts_lapsed']} events={churn['account_lapse_events']}",
        f"  accounts_resumed={churn['accounts_resumed']} events={churn['account_resume_events']}",
        '',
        'FUNNEL',
    ]
    if not any(data['funnel'].values()):
        lines.append('  # за период не записан ни один шаг: либо не было нажатий, либо выключен журнал')
    for step, count in data['funnel'].items():
        lines.append(f'  {step.lower()}={count}')
    lines += ['', 'COHORTS (signup month, lifetime to period end)']
    if not data['cohorts']:
        lines.append('  none')
    for cohort in data['cohorts']:
        lines.append(
            f"  {cohort['month']} users={cohort['users']} cash={cohort['cash']} revenue={cohort['revenue']} "
            f"cash_per_user={cohort['cash_per_user']} revenue_per_user={cohort['revenue_per_user']} "
            f"days_per_user={cohort['subscription_days_per_user']}"
        )
    return '\n'.join(lines)


def _format_counts(counts: dict) -> str:
    return ','.join(f'{key.lower()}={value}' for key, value in sorted(counts.items())) or 'none'
