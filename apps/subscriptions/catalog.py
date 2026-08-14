"""Что клиент получает в подписке — теми же словами, что и его приложение.

Экран бота называет страны до оплаты, а рендерер выбирает их заново при каждом
обновлении, из документа, которым распоряжается третья сторона. Совпасть эти
два списка могут ровно одним способом: обещание считывается из тех же строк,
которые уходят клиенту. Страна попадает сюда потому, что для неё отрисована
точка, — не потому, что кто-то вписал её в текст экрана, и не потому, что она
есть у провайдера.

Отсюда и форма: подписи разбираются из готовых строк, а не собираются заново.
Пересборка означала бы вторую копию правил выбора, а две копии расходятся —
и разойдутся они молча, обещанием страны, которой в подписке нет.

Ни один сбой здесь не имеет права стоить клиенту экрана. Любая неудача — сеть,
база, испорченный документ — возвращает пустой каталог, и экран тогда просто
ничего не обещает; это единственный честный ответ, когда список неизвестен.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import unquote

from apps.subscriptions.views import (
    _OWN_REGION_CODE,
    _WHITELIST_LABEL_SUFFIX,
    _backup_links,
    _endpoint_label,
    _is_backup_test_user,
)


logger = logging.getLogger(__name__)

# Номер-различитель, который рендерер приписывает второй одинаковой подписи:
# «🇳🇱 Нидерланды 2» — та же страна, что и «🇳🇱 Нидерланды», а не ещё одна.
_TRAILING_NUMBER = re.compile(r'\s+\d+$')


@dataclass(frozen=True)
class SubscriptionCatalog:
    """Страны подписки в порядке выдачи и те из них, что держат белый список.

    `whitelisted` — подмножество `countries`, а не отдельные подписи: в списке
    приложения такая строка называется «<страна> белые списки», так что страны
    хватает, чтобы её там найти, а подпись целиком читалась бы на экране как
    ещё одна страна с непонятным хвостом.
    """

    countries: tuple[str, ...] = ()
    whitelisted: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.countries)


def subscription_catalog(user_vpn=None) -> SubscriptionCatalog:
    """Каталог конкретной подписки или, без неё, подписки, которой ещё нет.

    Ходит в сеть и в базу, поэтому из асинхронного кода вызывается только
    через поток. Провайдерский документ читается из того же пятиминутного
    кэша, что и обычное обновление подписки, так что экран бота не добавляет
    провайдеру ни одного лишнего запроса сверх одного на кэш процесса.
    """
    try:
        labels = _own_labels(user_vpn)
        if _mirror_included(user_vpn):
            labels += [_line_remark(line) for line in (_backup_links() or [])]
        return _catalog_from_labels(labels)
    except Exception:
        logger.warning('subscription catalog unavailable; the screen will promise nothing')
        return SubscriptionCatalog()


def _catalog_from_labels(labels: list[str]) -> SubscriptionCatalog:
    """Свести подписи точек к списку стран, сохранив порядок выдачи.

    Порядок — тот, в котором строки лежат в подписке, потому что именно в нём
    клиент их и увидит; алфавит здесь был бы третьим порядком, не совпадающим
    ни с одним настоящим.
    """
    countries: list[str] = []
    whitelisted: list[str] = []
    for label in labels:
        bare = _TRAILING_NUMBER.sub('', label).strip()
        country = bare.removesuffix(_WHITELIST_LABEL_SUFFIX).strip()
        if not country:
            continue
        if country not in countries:
            countries.append(country)
        # Суффикс отделился — значит эта строка и есть обход белых списков.
        if bare != country and country not in whitelisted:
            whitelisted.append(country)
    return SubscriptionCatalog(tuple(countries), tuple(whitelisted))


def _line_remark(line: str) -> str:
    """Подпись точки — ровно та, что клиент прочитает в своём приложении."""
    return unquote(line.partition('#')[2])


def _own_labels(user_vpn) -> list[str]:
    """Подписи точек, за которые отвечает это развёртывание.

    Прямая линия рендерится всегда; линия обхода — только там, где у сервера
    настроен вход-ретранслятор. Подписке, которой ещё нет, её можно обещать
    лишь тогда, когда он настроен у каждого сервера, на который она может
    попасть: сервер выбирается случайно, и «есть хотя бы у одного» — это
    обещание, которое сбудется не для всех.
    """
    labels = [_endpoint_label(_OWN_REGION_CODE)]
    if _relay_configured(user_vpn):
        labels.append(_endpoint_label(_OWN_REGION_CODE, whitelisted=True))
    return labels


def _relay_configured(user_vpn) -> bool:
    if user_vpn is not None:
        return bool(user_vpn.server.client_vpn_host)
    from apps.servers.models import Server
    return Server.objects.exists() and not Server.objects.filter(client_vpn_host='').exists()


def _mirror_included(user_vpn) -> bool:
    """Достанутся ли этой подписке чужие точки.

    У подписки, которой ещё нет, нет и id, а гейт выкатки отвечает про id.
    Пока выкатка идёт по списку, новичку обещать нечего — в списке его нет;
    после перехода выкатки в состояние «всем» ответ перестаёт зависеть от id,
    и обещание становится верным ровно тогда же, когда становится верной сама
    выдача.
    """
    if user_vpn is not None:
        return _is_backup_test_user(user_vpn.id)
    from django.conf import settings
    return bool(getattr(settings, 'SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED', False)
                and getattr(settings, 'SUBSCRIPTION_BACKUP_ALL_USERS_ENABLED', False))
