"""Celery entrypoints for SPECIAL monitoring; no task restarts services."""

from __future__ import annotations

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import MonitorState, MonitorTransition
from .notifications import build_transition_payload, send_transition_notification
from .probes import LayerResult, run_control_plane_probe, run_host_capacity_probe, run_protocol_canary, run_regional_probe


def _notify_transition(transition_id: int) -> None:
    transition = MonitorTransition.objects.get(pk=transition_id)
    payload = build_transition_payload(
        layer=transition.layer,
        event=transition.event,
        error_class=transition.error_class,
        failures=transition.consecutive_failures,
        created_at=transition.created_at,
    )
    result = send_transition_notification(payload)
    transition.notification_attempted_at = timezone.now()
    transition.notification_delivered = result.delivered
    transition.notification_error_class = result.error_class
    transition.notification_destination_owner = settings.SPECIAL_MONITOR_PAGING_OWNER.strip()
    transition.save(
        update_fields=(
            'notification_attempted_at',
            'notification_delivered',
            'notification_error_class',
            'notification_destination_owner',
        )
    )


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
            transition = MonitorTransition.objects.create(
                layer=layer,
                event='opened' if alert else 'recovered',
                consecutive_failures=failures,
                error_class=result.error_class or '',
            )
            transaction.on_commit(lambda: notify_monitor_transition.delay(transition.pk))


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


@shared_task(name='apps.monitoring.tasks.run_host_capacity_monitor')
def run_host_capacity_monitor() -> dict[str, object]:
    return _run('host', run_host_capacity_probe)


@shared_task(name='apps.monitoring.tasks.notify_monitor_transition')
def notify_monitor_transition(transition_id: int) -> None:
    _notify_transition(transition_id)
