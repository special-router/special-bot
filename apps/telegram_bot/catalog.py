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


COUNTRIES_PREFIX: Final[str] = 'Страны в подписке: '

# Что именно даёт строка с пометкой. Без этой фразы пометка выглядит как ещё
# одна страна, и клиент выбирает её последней — то есть ровно тогда, когда она
# уже не поможет.
WHITELIST_HINT: Final[str] = (
    'продолжает работать, когда мобильный интернет режут до списка разрешённых сайтов.'
)


async def acatalog(user_vpn=None) -> SubscriptionCatalog:
    """Каталог из асинхронного обработчика: он ходит в сеть и в базу."""
    return await sync_to_async(subscription_catalog)(user_vpn)


def catalog_body(catalog: SubscriptionCatalog) -> list[str]:
    """Блоки экрана: страны и, отдельной строкой, обход белых списков.

    Пустой каталог даёт пустой список блоков, а не оговорку: экран, которому
    нечего сказать, молчит — обещать «страны появятся позже» значит обещать
    за провайдера.
    """
    if not catalog:
        return []
    blocks = [COUNTRIES_PREFIX + ', '.join(catalog.countries) + '.']
    blocks += [f'«{label}» {WHITELIST_HINT}' for label in catalog.whitelisted]
    return blocks
