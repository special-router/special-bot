"""Celery entrypoints for SPECIAL monitoring; no task restarts services."""

from __future__ import annotations

from celery import shared_task
from django.conf import settings
from django.db import transaction

from .models import MonitorState, MonitorTransition
from .probes import LayerResult, run_control_plane_probe, run_protocol_canary, run_regional_probe


def _record(layer: str, result: LayerResult) -> None:
    with transaction.atomic():
        state, _ = MonitorState.objects.select_for_update().get_or_create(layer=layer)
        previous_alert = state.alert
        failures = 0 if result.ok else state.consecutive_failures + 1
        alert = result.immediate or failures >= settings.SPECIAL_MONITOR_FAILURE_THRESHOLD
        state.last_ok = result.ok
        state.consecutive_failures = failures
        state.alert = alert
        state.error_class = result.error_class or ''
        state.details = result.details or {}
        state.save()
        if alert != previous_alert:
            MonitorTransition.objects.create(
                layer=layer,
                event='opened' if alert else 'recovered',
                consecutive_failures=failures,
                error_class=result.error_class or '',
            )


def _run(layer: str, function) -> dict[str, object]:
    try:
        result = function()
    except Exception:
        result = LayerResult(layer=layer, ok=False, error_class='runner_failure')
    _record(layer, result)
    return {'layer': layer, 'ok': result.ok, 'error_class': result.error_class}


@shared_task(name='apps.monitoring.tasks.run_control_plane_monitor')
def run_control_plane_monitor() -> dict[str, object]:
    return _run('l0', run_control_plane_probe)


@shared_task(name='apps.monitoring.tasks.run_regional_monitor')
def run_regional_monitor() -> dict[str, object]:
    return _run('l1', run_regional_probe)


@shared_task(name='apps.monitoring.tasks.run_protocol_monitor')
def run_protocol_monitor() -> dict[str, object]:
    return _run('l2', run_protocol_canary)
