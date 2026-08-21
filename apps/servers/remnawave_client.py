"""Реализация того же контракта, что и ``APIVPNClient``, поверх Remnawave.

Поверхность намеренно совпадает с 3x-ui-клиентом: ``add_user``, ``remove_user``,
``enable_user``, ``get_key``, ``get_raw_inbound_config``. Переключение между
панелями — это настройка, а не правка вызывающего кода, поэтому откат стоит
один перезапуск контейнера.

Расчётный период остаётся у нас. В панели у клиента стоит дальний срок, а
доступом управляет только ``status``: два независимых счётчика неизбежно
разъезжаются, и разъезжаются они в сторону отключённого оплатившего клиента.
Если бот умрёт, вместе с ним умрёт и списание, так что оставленный включённым
доступ — правильная сторона отказа.
"""
import logging
from datetime import timedelta
from typing import Final

from django.conf import settings
from django.utils.timezone import now

from apps.servers.models import Server
from apps.servers.remnawave import RemnawaveAPI, RemnawaveError
from apps.vpn.models import UserVPN


logger = logging.getLogger(__name__)

_FAR_FUTURE_DAYS: Final[int] = 3650


def _expire_at() -> str:
    days = int(getattr(settings, 'REMNAWAVE_EXPIRE_DAYS', _FAR_FUTURE_DAYS) or _FAR_FUTURE_DAYS)
    return (now() + timedelta(days=days)).isoformat().replace('+00:00', 'Z')


def remnawave_username(user_vpn: UserVPN) -> str:
    """Стабильный ключ клиента в панели.

    Через telegram_id, а не через UUID: имя видно в интерфейсе панели, и по нему
    должно быть понятно, кому писать. Тот же telegram_id уже уходит в remark
    выданной ссылки, так что это не новое раскрытие. Хвост из ``UserVPN.id``
    разводит две записи одного человека на разных серверах.
    """
    return f'tg_{user_vpn.user.telegram_id}_{user_vpn.id}'


def reality_params() -> dict:
    """Параметры Reality живой ноды.

    После вывода 3x-ui читать их неоткуда — конфиг ноды задаём мы сами, и эти
    значения совпадают с тем, что в него положено. Тот же приём, что у
    ``_grpc_config()``: настройка вместо запроса к панели на каждый вызов.
    """
    return {
        'public_key': str(getattr(settings, 'REMNAWAVE_REALITY_PUBLIC_KEY', '')),
        'server_name': str(getattr(settings, 'REMNAWAVE_REALITY_SERVER_NAME', '')),
        'short_id': str(getattr(settings, 'REMNAWAVE_REALITY_SHORT_ID', '')),
        'fingerprint': str(getattr(settings, 'REMNAWAVE_REALITY_FINGERPRINT', 'chrome')),
        'port': int(getattr(settings, 'REMNAWAVE_REALITY_PORT', 443) or 443),
    }


def _client_endpoint(client_vpn_host: str, inbound_port: int) -> tuple[str, int]:
    host, separator, configured_port = client_vpn_host.rpartition(':')
    if separator and configured_port.isdigit():
        return host, int(configured_port)
    return client_vpn_host, inbound_port


class RemnawaveVPNClient:
    def __init__(self, server: Server):
        self._server = server
        self._api = RemnawaveAPI()

    async def _find(self, user_vpn: UserVPN) -> dict | None:
        return await self._api.get_user_by_username(remnawave_username(user_vpn))

    async def _create(self, user_vpn: UserVPN) -> dict:
        limit = user_vpn.device_limit or getattr(settings, 'SUBSCRIPTION_DEVICE_LIMIT', 0)
        return await self._api.create_user(
            username=remnawave_username(user_vpn),
            expire_at=_expire_at(),
            vless_uuid=str(user_vpn.vpn_uuid),
            telegram_id=user_vpn.user.telegram_id,
            hwid_device_limit=limit or None,
            description=self._server.name,
            short_uuid=user_vpn.sub_id or '',
        )

    async def add_user(self, user_vpn: UserVPN):
        if await self._find(user_vpn) is None:
            await self._create(user_vpn)
            return
        await self.enable_user(user_vpn, enabled=True)

    async def remove_user(self, user_vpn: UserVPN):
        existing = await self._find(user_vpn)
        if existing is None:
            return
        await self._api.delete_user(existing['id'])

    async def enable_user(self, user_vpn: UserVPN, enabled: bool = True):
        existing = await self._find(user_vpn)
        if existing is None:
            if not enabled:
                # Отключать нечего, и заводить клиента ради отключения нельзя:
                # это создало бы доступ там, где его просили убрать.
                return
            await self._create(user_vpn)
            return
        await self._api.set_status(existing['id'], enabled=enabled)

    async def get_key(self, user_vpn: UserVPN) -> str:
        params = reality_params()
        if not params['public_key'] or not params['server_name']:
            raise RemnawaveError('reality parameters are not configured')
        client_host, port = _client_endpoint(user_vpn.server.client_vpn_host, params['port'])
        return (
            f"vless://{user_vpn.vpn_uuid}@{client_host}:{port}"
            f"?type=tcp&security=reality&pbk={params['public_key']}"
            f"&fp={params['fingerprint']}&sni={params['server_name']}"
            f"&sid={params['short_id']}&spx=%2F#{user_vpn.user.telegram_id}"
        )

    async def get_raw_inbound_config(self, user_vpn: UserVPN) -> dict:
        params = reality_params()
        address = self._server.client_vpn_host.split(':')[0]
        return {
            'outbounds': [
                {
                    'tag': 'proxy',
                    'protocol': 'vless',
                    'settings': {
                        'vnext': [
                            {
                                'address': address,
                                'port': params['port'],
                                'users': [
                                    {
                                        'id': str(user_vpn.vpn_uuid),
                                        'encryption': 'none',
                                        'flow': 'xtls-rprx-vision',
                                    }
                                ],
                            }
                        ]
                    },
                    'streamSettings': {
                        'sockopt': {'mark': 255},
                        'network': 'tcp',
                        'security': 'reality',
                        'realitySettings': {
                            'serverName': params['server_name'],
                            'fingerprint': params['fingerprint'],
                            'publicKey': params['public_key'],
                            'shortId': params['short_id'],
                            'spiderX': '/',
                        },
                    },
                }
            ]
        }
