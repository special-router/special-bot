import redis
from asgiref.sync import sync_to_async
from django.conf import settings
from telegram import Update
from telegram.ext import ContextTypes

from apps.analytics.funnel import subscription_created, subscription_refused_no_funds
from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.payments.models import Transaction
from apps.servers.models import Server, TariffServer
from apps.telegram_bot.handlers.balance import build_balance_screen
from apps.telegram_bot.handlers.show_keys import build_keys_screen
from apps.telegram_bot.ui import answer_query, render_screen
from apps.telegram_bot.utils import get_user
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN
from apps.vpn.services.add_vpn_to_user import add_vpn_to_user


async def add_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user: TelegramUser = await get_user(update)

    random_key: int = int(update.callback_query.data.split(':')[1])

    # проверка на повторное добавление ключа
    redis_client = redis.from_url(settings.REDIS_URL)
    redis_key = f'{random_key}.{user.id}'

    if redis_client.get(redis_key):
        # Второе нажатие за 15 секунд списание не повторяет, но и молчать
        # нельзя: без ответа оно выглядит ровно как несработавшее первое.
        await answer_query(update, 'Подписка уже добавляется.')
        return

    redis_client.set(redis_key, 1, 15)

    # Только сервер, на котором у пользователя ещё нет работающей подписки.
    # `add_vpn_to_user` возвращает уже существующую подписку сервера, а сервер
    # выбирался случайно из всех: при одном сервере каждое следующее нажатие
    # списывало сутки и возвращало ту же подписку. Ключ в Redis это не ловил —
    # он живёт 15 секунд и привязан к номеру нажатой кнопки, а экран после
    # добавления перерисовывается с новым номером.
    #
    # Отключённая подписка сервер не занимает: для неё нажатие — оплата
    # возобновления, и это единственный способ вернуть её в строй, потому что
    # ежедневное списание умеет только отключать.
    engaged = UserVPN.objects.filter_by_user(user_id=user.id).filter_by_enabled(True).values('server_id')
    server: Server = await Server.objects.with_related_tariffs().exclude(id__in=engaged).order_by_random().afirst()
    if server is None:
        await answer_query(update, 'Подписка уже активна, платить второй раз не нужно.')
        return

    # Денег не хватает — сразу показываем экран пополнения, а не отдельное
    # сообщение о балансе: следующий шаг пользователя всё равно там.
    if user.balance < server.tariff.price:
        text, keyboard = await build_balance_screen(user, notice='Недостаточно средств для новой подписки.')
        await render_screen(update, context, text, keyboard)
        await sync_to_async(subscription_refused_no_funds)(user.id, amount=server.tariff.price)
        return

    active_keys = await UserVPN.objects.filter_by_user(user_id=user.id).filter_by_enabled(True).acount()
    if active_keys >= settings.MAX_KEYS:
        await answer_query(
            update,
            f"Зарегистрировано максимальное количество подписок на аккаунт ({settings.MAX_KEYS}).",
        )
        return

    user_vpn = await add_vpn_to_user(user, server)
    await sync_to_async(subscription_created)(user.id, user_vpn.id)

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
    await render_screen(update, context, text, keyboard, toast='Подписка добавлена.')
