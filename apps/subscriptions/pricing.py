"""Сколько стоят сутки одной подписки.

Раньше вопроса не было: подписка стоила тариф, а второе устройство покупалось
второй подпиской. Теперь подписка у аккаунта одна, а устройств у неё сколько
угодно, и цена считается по местам: первые входят в тариф, каждое сверх стоит
ещё один тариф в сутки.

Одно место — одна функция, потому что цену спрашивают в трёх местах: ежедневное
списание, кнопка покупки места и экран, который эту цену показывает. Три
формулы разошлись бы, и разошлись бы молча — списание брало бы одно, а экран
обещал бы другое.
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings

from apps.subscriptions.devices import device_limit_for


# Места, входящие в тариф. Совпадает с лимитом по умолчанию не случайно: пока
# клиент не купил ни одного места, подписка стоит ровно тариф, как и раньше.
DEFAULT_FREE_DEVICE_SLOTS = 2


def free_device_slots() -> int:
    """Сколько устройств входит в цену подписки."""
    try:
        value = int(getattr(settings, 'SUBSCRIPTION_FREE_DEVICE_SLOTS', DEFAULT_FREE_DEVICE_SLOTS))
    except (TypeError, ValueError):
        return DEFAULT_FREE_DEVICE_SLOTS
    # Ноль бесплатных мест сделал бы платной саму подписку сверх тарифа, а
    # потолок держит цену конечной, если настройку однажды впишут неверно.
    return min(max(value, 1), 32)


def paid_device_slots(user_vpn) -> int:
    """Места сверх бесплатных — то, за что берётся надбавка."""
    return max(0, device_limit_for(user_vpn) - free_device_slots())


def daily_price(user_vpn) -> Decimal:
    """Суточная цена подписки со всеми её местами.

    Тариф берётся с сервера подписки, а не из единственной строки `TariffServer`:
    цена принадлежит серверу, и списание должно спрашивать ровно то, что
    проверила кнопка.
    """
    tariff = user_vpn.server.tariff.price
    return tariff * (1 + paid_device_slots(user_vpn))


def slot_price(user_vpn) -> Decimal:
    """Цена одного дополнительного места в сутки."""
    return user_vpn.server.tariff.price
