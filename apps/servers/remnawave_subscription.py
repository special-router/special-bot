"""Выдача ссылки подписки без 3x-ui.

``sub_id`` уже лежит в нашей базе — он же ``shortUuid`` в панели, и адрес
подписки собирается из него одной строкой.

Панель здесь не спрашивается вообще, и это осознанно. Ответ ``/sub/`` строит наш
же Django из своей базы и своих настроек — доступность панели на него не влияет.
Значит, любой запрос к панели на этом пути добавляет только один исход: панель
недоступна, вызывающий код ловит исключение, срабатывает предохранитель, и
клиент получает прямой ключ — один сервер вместо четырнадцати. Ровно так и
случилось 2026-08-21.

Расхождение нашей записи с панелью ловится там, где для этого есть повод и
расписание, — пробой control plane (``entitled_missing``), а не на каждом
открытии экрана «Подписки».
"""
from apps.servers.subscription_connector import (
    SubscriptionClientMissing,
    SubscriptionReference,
    build_subscription_url,
)
from apps.vpn.models import UserVPN
from django.conf import settings


def _reference(sub_id: str) -> SubscriptionReference:
    return SubscriptionReference(
        sub_id=sub_id,
        url=build_subscription_url(settings.SUBSCRIPTION_BASE_URL, sub_id),
    )


async def subscription_reference(user_vpn: UserVPN) -> SubscriptionReference:
    """Ссылка по присвоенному ``sub_id``; ничего не меняет и никуда не ходит.

    Читается ровно одно поле самой записи. Ленивые связи не трогаются: в
    async-контексте обращение к неподгруженной связи бросает
    ``SynchronousOnlyOperation``, а вызывающий код принимает это за аварию
    панели и молча выдаёт прямой ключ.

    ``shortUuid`` в Remnawave задаётся только при создании, поэтому присвоить
    недостающий ``sub_id`` задним числом нельзя: такая запись чинится
    пересозданием клиента в панели, а не здесь.
    """
    sub_id = getattr(user_vpn, 'sub_id', '') or ''
    if not sub_id:
        raise SubscriptionClientMissing('no subscription id assigned')
    return _reference(sub_id)
