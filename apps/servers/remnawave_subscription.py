"""Выдача ссылки подписки без 3x-ui.

``sub_id`` уже лежит в нашей базе — он же ``shortUuid`` в панели. Ходить за ним
в панель незачем: адрес подписки строится из того, что у нас есть, а лишний
сетевой запрос на каждой выдаче ключа означает, что недоступность панели
превращается в «клиент получил один сервер вместо четырнадцати».

Панель здесь спрашивается ровно об одном — существует ли клиент. Отсутствие
``shortUuid`` у нас при живой панели значит, что запись рассинхронизирована, и
об этом надо сказать, а не молча выдать прямой ключ.
"""
import logging

from django.conf import settings

from apps.servers.remnawave import RemnawaveAPI
from apps.servers.remnawave_client import remnawave_username
from apps.servers.subscription_connector import (
    SubscriptionClientMissing,
    SubscriptionReference,
    build_subscription_url,
)
from apps.vpn.models import UserVPN


logger = logging.getLogger(__name__)


def _reference(sub_id: str) -> SubscriptionReference:
    return SubscriptionReference(
        sub_id=sub_id,
        url=build_subscription_url(settings.SUBSCRIPTION_BASE_URL, sub_id),
    )


async def get_existing_subscription_reference(user_vpn: UserVPN) -> SubscriptionReference:
    """Ссылка по уже присвоенному ``sub_id``; ничего не меняет.

    Экраны просмотра ходят сюда, поэтому открытие профиля не может создать новую
    личность подписки.
    """
    sub_id = getattr(user_vpn, 'sub_id', '') or ''
    if not sub_id:
        raise SubscriptionClientMissing('no subscription id assigned')
    return _reference(sub_id)


async def ensure_subscription_reference(user_vpn: UserVPN) -> SubscriptionReference:
    """То же, но с проверкой, что панель знает этого клиента.

    ``shortUuid`` в Remnawave задаётся только при создании, поэтому присвоить
    его задним числом нельзя — недостающий ``sub_id`` чинится пересозданием
    клиента в панели, а не здесь. Молча выдать что-нибудь означало бы выдать
    ссылку, которая ведёт в никуда.
    """
    sub_id = getattr(user_vpn, 'sub_id', '') or ''
    if not sub_id:
        raise SubscriptionClientMissing('no subscription id assigned')

    panel_user = await RemnawaveAPI().get_user_by_username(remnawave_username(user_vpn))
    if panel_user is None:
        raise SubscriptionClientMissing('client is unknown to the control plane')

    return _reference(sub_id)
