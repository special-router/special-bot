"""Абзац о содержимом подписки для экранов бота.

Список стран приходит из `apps.subscriptions.catalog`, где он считан из тех же
строк, что уходят в приложение клиента. Здесь остаётся только текст — и он
никогда не называет страну сам, потому что написанная руками страна пережила
бы день, когда провайдер её убрал.

Абзац показывается до оплаты: «что я получу» — вопрос, на который клиент
отвечает раньше, чем нажимает «Пополнить», и статичное «Нидерланды» отвечало
на него неверно с тех пор, как в подписке появилось восемь других стран.
"""
from __future__ import annotations

from typing import Final

from asgiref.sync import sync_to_async

from apps.subscriptions.catalog import SubscriptionCatalog, subscription_catalog
from apps.telegram_bot.ui import bold


COUNTRIES_LABEL: Final[str] = 'Страны:'
WHITELIST_LABEL: Final[str] = 'Белые списки:'


async def acatalog(user_vpn=None) -> SubscriptionCatalog:
    """Каталог из асинхронного обработчика: он ходит в сеть и в базу."""
    return await sync_to_async(subscription_catalog)(user_vpn)


def catalog_body(catalog: SubscriptionCatalog) -> list[str]:
    """Один блок в две строки: страны и те из них, что держат белые списки.

    Подпись выделена, дальше перечисление — экран перечисляет, а не объясняет.
    Что даёт вторая строка, клиент видит по самой подписке; абзац про глушилки
    здесь был текстом, который читают один раз и пролистывают всегда.

    Пустой каталог даёт пустой список блоков, а не оговорку: экран, которому
    нечего сказать, молчит — обещать «страны появятся позже» значит обещать
    за провайдера.
    """
    if not catalog:
        return []
    lines = [f'{bold(COUNTRIES_LABEL)} ' + ', '.join(catalog.countries)]
    if catalog.whitelisted:
        lines.append(f'{bold(WHITELIST_LABEL)} ' + ', '.join(catalog.whitelisted))
    return ['\n'.join(lines)]
