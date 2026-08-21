"""Инвентарь control plane, читаемый из Remnawave.

Тот же ответ, что раньше давал 3x-ui: какие inbound-ы объявлены и какие клиенты
на них заведены. Мониторинг L0 сравнивает это с тем, кто оплатил, и именно он
ловит случай «человек заплатил, а доступа нет». После переезда 3x-ui читать
нечего, поэтому источник заменён, а форма ответа сохранена — иначе пришлось бы
переписывать и пробу, и её тесты, и порог тревоги.

Панель — единственный источник: она же и раздаёт конфиг ноде, так что
расхождение между тем, что мы прочитали, и тем, что работает, невозможно.
"""
import asyncio
from dataclasses import dataclass
from typing import Any, Final

from django.conf import settings

from apps.servers.models import Server
from apps.servers.remnawave import RemnawaveAPI


_PAGE_SIZE: Final[int] = 500
_ACTIVE: Final[str] = 'ACTIVE'


@dataclass(frozen=True)
class InboundSnapshot:
    """Совпадает по полям со снимком 3x-ui, который читает проба L0."""

    server_id: int
    server_name: str
    inbound_id: int
    port: int
    protocol: str
    network: str
    security: str
    clients: int
    enabled_clients: int
    with_sub_id: int
    missing_sub_id: int


async def _fetch_users(api: RemnawaveAPI) -> list[dict[str, Any]]:
    """Все пользователи панели одним снимком.

    Страницы берутся до исчерпания: частичный список означал бы «клиент
    пропал», а это ровно та тревога, которую проба поднимает.
    """
    collected: list[dict[str, Any]] = []
    start = 0
    while True:
        page = await api.request_json('GET', f'/api/users?size={_PAGE_SIZE}&start={start}')
        users = page.get('users') if isinstance(page, dict) else None
        if not users:
            break
        collected.extend(users)
        total = int(page.get('total') or 0)
        start += len(users)
        if start >= total or len(users) < _PAGE_SIZE:
            break
    return collected


async def _fetch_inbounds(api: RemnawaveAPI) -> list[dict[str, Any]]:
    """Inbound-ы всех профилей конфигурации.

    Берутся из сырого ``config``, а не из сводки ``inbounds``: тревога по дрейфу
    инвентаря должна срабатывать на том, что реально уедет на ноду.
    """
    payload = await api.request_json('GET', '/api/config-profiles')
    profiles = payload.get('configProfiles') if isinstance(payload, dict) else None
    inbounds: list[dict[str, Any]] = []
    for profile in profiles or []:
        config = profile.get('config') or {}
        inbounds.extend(config.get('inbounds') or [])
    return inbounds


def _inbound_id(server: Server, inbound: dict[str, Any]) -> int:
    """Стабильный номер inbound-а в терминах ``SPECIAL_MONITOR_EXPECTED_INBOUNDS``.

    У Remnawave собственных числовых id нет, а порядковый номер в профиле
    меняется от любой перестановки и выглядел бы как дрейф инвентаря. Поэтому
    inbound опознаётся по порту: ожидания в настройках задают порт, и именно он
    определяет, куда попадёт клиент.
    """
    port = int(inbound.get('port') or 0)
    for expected in getattr(settings, 'SPECIAL_MONITOR_EXPECTED_INBOUNDS', []) or []:
        if int(expected.get('server_id', -1)) != server.id:
            continue
        if int(expected.get('port', -1)) == port:
            return int(expected.get('inbound_id', 0))
    return port


def _inbound_row(server: Server, inbound: dict[str, Any],
                 users: list[dict[str, Any]]) -> InboundSnapshot:
    stream = inbound.get('streamSettings') or {}
    active = [user for user in users if str(user.get('status')) == _ACTIVE]
    # Панель раздаёт клиентов на inbound через отряды, а не поштучно, поэтому
    # число клиентов у всех inbound-ов профиля одно и то же. Это не потеря
    # точности: проба сверяет оплативших с включёнными, а не с раскладкой по
    # транспортам.
    return InboundSnapshot(
        server_id=server.id,
        server_name=server.name,
        inbound_id=_inbound_id(server, inbound),
        port=int(inbound.get('port') or 0),
        protocol=str(inbound.get('protocol') or ''),
        network=str(stream.get('network') or ''),
        security=str(stream.get('security') or ''),
        clients=len(users),
        enabled_clients=len(active),
        with_sub_id=sum(bool(user.get('shortUuid')) for user in active),
        missing_sub_id=sum(not bool(user.get('shortUuid')) for user in active),
    )


async def _snapshot_once(server: Server) -> list[InboundSnapshot]:
    api = RemnawaveAPI()
    users = await _fetch_users(api)
    inbounds = await _fetch_inbounds(api)
    rows = [_inbound_row(server, inbound, users) for inbound in inbounds]
    return sorted(rows, key=lambda item: (item.server_id, item.port, item.inbound_id))


async def fetch_inbound_snapshots(server: Server) -> list[InboundSnapshot]:
    """Устойчивый снимок инвентаря: два одинаковых чтения подряд.

    Требование то же, что было к 3x-ui: одиночное неполное чтение не должно
    выглядеть как дрейф control plane и будить дежурного.
    """
    max_attempts = settings.XUI_CONTROL_PLANE_READ_ATTEMPTS
    backoff = settings.XUI_CONTROL_PLANE_READ_BACKOFF
    if max_attempts < 2:
        raise RuntimeError('Control plane consistency requires at least two read attempts.')

    previous = None
    for attempt in range(1, max_attempts + 1):
        try:
            current = await _snapshot_once(server)
            if previous is not None and current == previous:
                return current
            previous = current
        except Exception:
            previous = None
            if attempt == max_attempts:
                raise
        if attempt < max_attempts:
            await asyncio.sleep(backoff)

    raise RuntimeError(
        f'Control plane inventory consistency could not be established for server_id={server.id} '
        f'after {max_attempts} attempts.'
    )


async def _client_ids_once() -> tuple[set[str], set[str]]:
    api = RemnawaveAPI()
    users = await _fetch_users(api)
    all_ids = {str(user.get('vlessUuid') or '') for user in users}
    enabled_ids = {
        str(user.get('vlessUuid') or '')
        for user in users
        if str(user.get('status')) == _ACTIVE
    }
    all_ids.discard('')
    enabled_ids.discard('')
    return all_ids, enabled_ids


async def fetch_control_plane_client_ids(server: Server) -> tuple[set[str], set[str]]:
    """Все и включённые UUID клиентов панели, без записи.

    Сигнатура и поведение повторяют 3x-ui-версию: два совпавших чтения, иначе
    исключение. ``server`` не используется — панель одна на весь стенд, но
    параметр оставлен, чтобы вызывающая сторона не менялась.
    """
    max_attempts = settings.XUI_CONTROL_PLANE_READ_ATTEMPTS
    backoff = settings.XUI_CONTROL_PLANE_READ_BACKOFF
    if max_attempts < 2:
        raise RuntimeError('Control plane consistency requires at least two read attempts.')

    previous = None
    for attempt in range(1, max_attempts + 1):
        try:
            current = await _client_ids_once()
            if previous is not None and current == previous:
                return current
            previous = current
            if attempt < max_attempts:
                await asyncio.sleep(backoff)
        except Exception:
            previous = None
            if attempt == max_attempts:
                raise
            await asyncio.sleep(backoff)

    raise RuntimeError(
        f'Control plane consistency could not be established for server_id={server.id} '
        f'after {max_attempts} attempts. Last read: {len(previous[0]) if previous else 0} total, '
        f'{len(previous[1]) if previous else 0} enabled.'
    )
