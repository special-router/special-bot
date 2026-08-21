"""Тонкий клиент Remnawave API.

Намеренно не SDK. Контракт панели дрейфует между версиями — поле отряда звалось
``squadUuids``, потом ``activeInternalSquads``, стратегия сброса — ``NORESET``,
потом ``NO_RESET``. SDK прячет этот дрейф за своей версией и ломается молча;
здесь имя поля лежит в настройке, а неизвестный ответ поднимает ошибку с телом,
по которому видно, что именно панель не приняла.

Ничего из того, что здесь ходит, не попадает в логи: токен — это доступ к
выдаче ключей всем клиентам, ссылка подписки — доступ к трафику одного.
"""
import logging
from typing import Any, Final

import httpx
from django.conf import settings


logger = logging.getLogger(__name__)

# Ответ панели заворачивается в {"response": {...}} на всех эндпоинтах.
_ENVELOPE: Final[str] = 'response'
_TIMEOUT: Final[float] = 15.0


class RemnawaveError(RuntimeError):
    """Панель ответила не тем, чего мы ждали."""


def _base_url() -> str:
    return str(getattr(settings, 'REMNAWAVE_API_URL', '')).rstrip('/')


def _token() -> str:
    return str(getattr(settings, 'REMNAWAVE_API_TOKEN', ''))


def configured() -> bool:
    return bool(_base_url() and _token())


def _squad_field() -> str:
    return str(getattr(settings, 'REMNAWAVE_SQUAD_FIELD', 'activeInternalSquads'))


def _squads() -> list[str]:
    value = getattr(settings, 'REMNAWAVE_SQUAD_UUIDS', []) or []
    return [str(item) for item in value if str(item).strip()]


def _headers() -> dict[str, str]:
    return {
        'Authorization': f'Bearer {_token()}',
        'Content-Type': 'application/json',
        # Панель за реверс-прокси иногда фильтрует запросы без него.
        'X-Forwarded-For': '127.0.0.1',
        'X-Forwarded-Proto': 'https',
    }


def _unwrap(payload: Any) -> dict:
    if isinstance(payload, dict) and _ENVELOPE in payload:
        payload = payload[_ENVELOPE]
    if not isinstance(payload, dict):
        raise RemnawaveError('unexpected payload shape')
    return payload


class RemnawaveAPI:
    """Обёртка над теми пятью операциями, которые нужны боту."""

    def __init__(self, *, base_url: str = '', token: str = ''):
        self._base_url = (base_url or _base_url()).rstrip('/')
        self._token = token or _token()
        if not self._base_url or not self._token:
            raise RemnawaveError('remnawave is not configured')

    async def _request(self, method: str, path: str, *, json_body: dict | None = None,
                       allow_404: bool = False) -> dict | None:
        url = f'{self._base_url}{path}'
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.request(method, url, headers=_headers(), json=json_body)
        if allow_404 and response.status_code == 404:
            return None
        if response.status_code >= 400:
            # Тело нужно целиком: именно в нём панель называет поле, которое не
            # приняла. Токена и ссылок подписки в ошибке валидации не бывает.
            raise RemnawaveError(
                f'{method} {path} -> {response.status_code}: {response.text[:400]}')
        if not response.content:
            return None
        return _unwrap(response.json())

    async def request_json(self, method: str, path: str, *, json_body: dict | None = None) -> dict:
        """Сырой доступ к эндпоинту панели для чтения.

        Инвентаризация мониторинга ходит по эндпоинтам, которых нет среди пяти
        операций бота. Дублировать ради них транспорт значило бы завести второй
        путь с собственной обработкой ошибок и заголовков.
        """
        payload = await self._request(method, path, json_body=json_body)
        return payload if isinstance(payload, dict) else {}

    async def get_user_by_username(self, username: str) -> dict | None:
        return await self._request('GET', f'/api/users/by-username/{username}', allow_404=True)

    async def create_user(self, *, username: str, expire_at: str, vless_uuid: str,
                          telegram_id: int | None = None, hwid_device_limit: int | None = None,
                          description: str = '', short_uuid: str = '') -> dict:
        body: dict[str, Any] = {
            'username': username,
            'expireAt': expire_at,
            # UUID переносится как есть: с тем же Reality-ключом на ноде уже
            # выданные клиентам ссылки продолжают работать после переключения.
            'vlessUuid': vless_uuid,
            'trafficLimitBytes': 0,
            'status': 'ACTIVE',
        }
        if short_uuid:
            # Панель раздаёт подписку по ``shortUuid``, наш прокси ходит по
            # ``sub_id``. Приравниваем их на создании — иначе понадобилась бы
            # таблица соответствий, которая расходится ровно тогда, когда её
            # некому чинить.
            #
            # Задать его можно только здесь: PATCH ``shortUuid`` панель
            # принимает и молча игнорирует. Значение вне её формата (старые
            # 16-символьные не-hex subId из 3x-ui) она так же молча заменяет на
            # своё, поэтому после создания равенство надо проверять, а не
            # предполагать.
            body['shortUuid'] = short_uuid
        squads = _squads()
        if squads:
            body[_squad_field()] = squads
        if telegram_id is not None:
            body['telegramId'] = int(telegram_id)
        if hwid_device_limit:
            body['hwidDeviceLimit'] = int(hwid_device_limit)
        if description:
            body['description'] = description
        created = await self._request('POST', '/api/users', json_body=body)
        if created is None:
            raise RemnawaveError('create returned an empty body')
        return created

    async def update_user(self, user_id: int, **fields: Any) -> dict | None:
        # Опознаётся целочисленным ``id``. Поля ``uuid`` у пользователя нет —
        # проверено на живой панели 3.x; запрос с ним падает на валидации.
        body: dict[str, Any] = {'id': int(user_id)}
        body.update(fields)
        return await self._request('PATCH', '/api/users', json_body=body)

    async def set_status(self, user_id: int, *, enabled: bool) -> dict | None:
        return await self.update_user(user_id, status='ACTIVE' if enabled else 'DISABLED')

    async def delete_user(self, user_id: int) -> None:
        await self._request('DELETE', f'/api/users/{int(user_id)}', allow_404=True)
