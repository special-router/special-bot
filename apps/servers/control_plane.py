"""Единственная точка выбора control plane для чтения инвентаря.

Выдача доступа переключается флагом ``REMNAWAVE_ENABLED`` через
``vpn_client_for``. Мониторинг обязан смотреть в ту же панель: иначе после
отката флага проба L0 сверяла бы оплативших со списком клиентов панели, которая
никого уже не обслуживает, и молчала бы ровно в аварии, ради которой заведена.
"""
from django.conf import settings

from apps.servers.models import Server


def _remnawave_enabled() -> bool:
    return bool(getattr(settings, 'REMNAWAVE_ENABLED', False))


async def fetch_inbound_snapshots(server: Server):
    """Снимок инвентаря inbound-ов действующей панели."""
    if _remnawave_enabled():
        from apps.servers.remnawave_inventory import fetch_inbound_snapshots as _fetch
    else:
        from apps.servers.management.commands.audit_xui_inbounds import fetch_inbound_snapshots as _fetch
    return await _fetch(server)


async def fetch_control_plane_client_ids(server: Server) -> tuple[set[str], set[str]]:
    """Все и включённые UUID клиентов действующей панели."""
    if _remnawave_enabled():
        from apps.servers.remnawave_inventory import fetch_control_plane_client_ids as _fetch
    else:
        from apps.vpn.management.commands.audit_legacy_vpn import fetch_control_plane_client_ids as _fetch
    return await _fetch(server)
