"""Read-only monitoring dashboard for the in-bot admin panel.

`MonitorState.details` can carry counts, booleans and latencies for any layer
(see `apps.monitoring.probes`) — never a bearer URL or client UUID, by
inspection of every probe as of 2026-08-14. It is rendered here only through a
per-layer allowlist of known-safe keys rather than dumped, so that stays true
even if a future probe adds a field it should not.
"""
from __future__ import annotations

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from apps.monitoring.models import MonitorState, MonitorTransition
from apps.telegram_bot.admin_auth import admin_only
from apps.telegram_bot.ui import back_button, bold, button, render_screen, screen


LAYER_LABELS = {
    'l0': 'L0 — control plane',
    'l1': 'L1 — региональные пробы',
    'l2': 'L2 — протокольный канар',
    'host': 'Host — ёмкость хоста',
    'checkout': 'Checkout — путь оплаты',
}

TRANSITIONS_SHOWN = 10


def _details_summary(layer: str, details) -> str | None:
    """Known-safe fields only, per layer — never the raw JSON blob."""
    if not isinstance(details, dict):
        return None

    if layer == 'l0':
        parts = []
        inbounds = details.get('inbounds')
        if isinstance(inbounds, list):
            parts.append(f'inbound-ов: {len(inbounds)}')
        if details.get('inventory_drift'):
            parts.append('инвентарь разошёлся')
        return ', '.join(parts) or None

    if layer == 'l1':
        endpoints = details.get('endpoints')
        if isinstance(endpoints, list):
            ok = sum(1 for item in endpoints if isinstance(item, dict) and item.get('ok'))
            return f'endpoint-ов: {len(endpoints)}, живых: {ok}'
        return None

    if layer == 'l2':
        if details.get('status') == 'disabled':
            return 'слой выключен'
        parts = []
        if 'subscription_e2e' in details:
            parts.append(f'подписка: {"ok" if details.get("subscription_e2e") else "fail"}')
        if 'direct_legacy_e2e' in details:
            parts.append(f'прямой ключ: {"ok" if details.get("direct_legacy_e2e") else "fail"}')
        return ', '.join(parts) or None

    if layer == 'host':
        labels = (
            ('mem_available_mb', 'память, МБ'),
            ('swap_used_mb', 'своп занят, МБ'),
            ('load1_per_cpu', 'нагрузка/cpu'),
            ('oom_kills', 'oom'),
        )
        parts = [f'{label}: {details[key]}' for key, label in labels if key in details]
        return ', '.join(parts) or None

    if layer == 'checkout':
        parts = []
        if 'tariff_ok' in details:
            parts.append(f'тариф: {"ok" if details["tariff_ok"] else "fail"}')
        if 'invoice_ok' in details:
            parts.append(f'счёт: {"ok" if details["invoice_ok"] else "fail"}')
        if 'cash_gap_days' in details:
            parts.append(f'дней без оплат: {details["cash_gap_days"]}')
        return ', '.join(parts) or None

    return None


async def build_monitor_dashboard() -> tuple[str, InlineKeyboardMarkup]:
    states = [state async for state in MonitorState.objects.order_by('layer')]

    body: list[str] = []
    buttons: list[list] = []
    for state in states:
        indicator = '✅' if state.last_ok and not state.alert else '❌'
        label = LAYER_LABELS.get(state.layer, state.layer)
        line = f'{indicator} {bold(label)} — сбоев подряд: {state.consecutive_failures}'
        if state.error_class:
            line += f', {state.error_class}'
        summary = _details_summary(state.layer, state.details)
        if summary:
            line += f'\n{summary}'
        line += f'\nПроверено: {state.checked_at:%Y-%m-%d %H:%M} UTC'
        body.append(line)
        buttons.append([button(f'История: {label}', f'admin_monitor_layer:{state.layer}')])

    if not states:
        body.append('Нет данных мониторинга.')
    buttons.append([back_button('admin_menu')])

    text = screen('Мониторинг', body=body)
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@admin_only
async def admin_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, keyboard = await build_monitor_dashboard()
    await render_screen(update, context, text, keyboard)


@admin_only
async def admin_monitor_layer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    layer = update.callback_query.data.split(':', 1)[1]
    transitions = [
        transition
        async for transition in MonitorTransition.objects.filter(layer=layer).order_by('-created_at')[:TRANSITIONS_SHOWN]
    ]
    label = LAYER_LABELS.get(layer, layer)

    if not transitions:
        body = ['Переходов не было.']
    else:
        body = [
            f'{"открыт" if t.event == "opened" else "восстановлено"} — {t.error_class or "—"} — '
            f'{t.created_at:%Y-%m-%d %H:%M} UTC'
            for t in transitions
        ]

    text = screen(f'История: {label}', body=body)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[back_button('admin_monitor')]])
    await render_screen(update, context, text, keyboard)
