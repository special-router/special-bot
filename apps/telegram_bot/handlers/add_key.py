import redis
from django.conf import settings
from telegram import Update
from telegram.ext import ContextTypes

from apps.payments.choices import TransactionStatusChoices, TransactionSourceChoices
from apps.payments.models import Transaction
from apps.servers.models import Server, TariffServer
from apps.telegram_bot.handlers.balance import show_balance
from apps.telegram_bot.handlers.show_keys import show_keys
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

    # отправить пользователю сообщение о том, что у него нет баланса (просто инфу о балансе вывести)
    if user.balance < server.tariff.price:
        await update.callback_query.answer(
            text=f"Недостаточно средств. Пополните баланс.",
        )
        return await show_balance(update, context)

    if await UserVPN.objects.filter_by_user(user_id=user.id).acount() >= settings.MAX_KEYS:
        await update.callback_query.answer(
            text=f"Зарегистрировано максимальное количество ключей на аккаунт ({settings.MAX_KEYS}).",
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

    await show_keys(update, context)
