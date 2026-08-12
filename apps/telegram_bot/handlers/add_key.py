import redis
from django.conf import settings
from telegram import Update
from telegram.ext import ContextTypes

from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.payments.models import Transaction
from apps.servers.models import Server, TariffServer
from apps.telegram_bot.handlers.balance import build_balance_screen
from apps.telegram_bot.handlers.show_keys import build_keys_screen
from apps.telegram_bot.ui import render_screen
from apps.telegram_bot.utils import get_user
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN
from apps.vpn.services.add_vpn_to_user import add_vpn_to_user


async def add_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user: TelegramUser = await get_user(update)
    server: Server = await Server.objects.with_related_tariffs().order_by_random().afirst()

    random_key: int = int(update.callback_query.data.split(':')[1])

    # проверка на повторное добавление ключа
    redis_client = redis.from_url(settings.REDIS_URL)
    redis_key = f'{random_key}.{user.id}'

    if redis_client.get(redis_key):
        return

    redis_client.set(redis_key, 1, 15)

    # Денег не хватает — сразу показываем экран пополнения, а не отдельное
    # сообщение о балансе: следующий шаг пользователя всё равно там.
    if user.balance < server.tariff.price:
        text, keyboard = await build_balance_screen(user, notice='Недостаточно средств для новой подписки.')
        await render_screen(update, context, text, keyboard)
        return

    active_keys = await UserVPN.objects.filter_by_user(user_id=user.id).filter_by_enabled(True).acount()
    if active_keys >= settings.MAX_KEYS:
        await update.callback_query.answer(
            text=f"Зарегистрировано максимальное количество подписок на аккаунт ({settings.MAX_KEYS}).",
        )
        return

    await add_vpn_to_user(user, server)

    tariff: TariffServer = await TariffServer.objects.aget()

    await Transaction.objects.acreate(
        user=user,
        amount=-tariff.price,
        status=TransactionStatusChoices.SUCCESS,
        source=TransactionSourceChoices.BUY,
    )

    # Баланс аннотирован до списания, поэтому пользователь перечитывается —
    # иначе экран показал бы сумму, которой уже нет.
    text, keyboard = await build_keys_screen(await get_user(update), notice='Подписка добавлена.')
    await render_screen(update, context, text, keyboard)
