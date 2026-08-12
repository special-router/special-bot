import asyncio
import contextlib
import datetime
import logging
import time
from collections import defaultdict

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from telegram import Bot

from apps.analytics.funnel import subscription_disabled_no_funds
from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.payments.models import Transaction
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN
from apps.vpn.services.remove_vpn_user_from_server import disable_vpn_user_from_server


logger = logging.getLogger(__name__)

OUT_OF_MONEY_TEXT = 'Закончились деньги на балансе. Доступ к услугам остановлен'
LOW_BALANCE_TEXT = 'Пополните баланс, денег осталось на 1 день'

# Beat запускает задачу в 00:00 UTC, но старт может прийтись на несколько миллисекунд
# раньше полуночи (дрейф beat, расхождение часов воркера). Дата «как есть» дала бы тогда
# вчерашний ключ идемпотентности: прогон увидел бы все подписки уже списанными и пропустил
# бы сутки целиком по всем аккаунтам. Поэтому граница суток сдвигается на этот допуск —
# старт в последние минуты дня относится к наступающим суткам.
EARLY_START_TOLERANCE = datetime.timedelta(minutes=5)


def _notify(bot: Bot, telegram_id: int, text: str):
    # todo: добавить вывод логов
    with contextlib.suppress(Exception):
        asyncio.run(bot.send_message(chat_id=telegram_id, text=text))
        # это чтобы слишком часто сообщения в телегу не отправлять
        time.sleep(1)


def _charge_date(now: datetime.datetime) -> datetime.date:
    """Дата-ключ идемпотентности прогона, устойчивая к старту около полуночи."""
    return (now.astimezone(datetime.timezone.utc) + EARLY_START_TOLERANCE).date()


def _charge_user(user_id: int, subscriptions: list[UserVPN], charge_date: datetime.date):
    """Списать дневную плату по всем подпискам одного аккаунта.

    Баланс общий на аккаунт, поэтому он читается один раз и уменьшается по ходу цикла:
    решение по каждой подписке принимается по живому остатку, а не по значению до цикла.
    Подписки финансируются в порядке created_at, и как только денег не хватило на очередную,
    она и все более новые отключаются — даже если какая-то из них дешевле и формально была бы
    оплачена. Иначе новая дешевая подписка вытесняла бы более старую дорогую. Списание
    никогда не уводит баланс в минус: если остатка не хватает, транзакция не создается вовсе.

    Каждое списание идет отдельным вложенным atomic-блоком, поэтому сбой на одной подписке
    (например, удаленной пользователем прямо во время прогона) не отменяет списания,
    уже сделанные по остальным подпискам аккаунта.

    Возвращает (подписки к отключению, одно сообщение аккаунту или None) для исполнения
    уже после коммита: сеть панели и телеграма не должна удерживать открытую денежную
    транзакцию.
    """
    to_disable = []
    charged = 0

    with transaction.atomic():
        # Внешняя транзакция существует ради блокировки аккаунта: она сериализует
        # параллельные прогоны биллинга по одному балансу. Изоляцию сбоев дает не она,
        # а вложенные atomic-блоки внутри цикла.
        user = TelegramUser.objects.select_for_update().get(id=user_id)

        balance = TelegramUser.objects.filter(id=user_id).annotate_balance().values_list('balance', flat=True).first()
        already_charged = set(
            Transaction.objects.filter_by_source(TransactionSourceChoices.EVERYDAY_SYSTEM)
            .filter(charge_date=charge_date, user_vpn__in=subscriptions)
            .values_list('user_vpn_id', flat=True)
        )
        prices = {user_vpn.id: user_vpn.server.tariff.price for user_vpn in subscriptions}

        for position, user_vpn in enumerate(subscriptions):
            if user_vpn.id in already_charged:
                continue

            if balance < prices[user_vpn.id]:
                to_disable.extend(
                    remaining for remaining in subscriptions[position:] if remaining.id not in already_charged
                )
                break

            try:
                with transaction.atomic():
                    Transaction.objects.create(
                        user=user,
                        user_vpn=user_vpn,
                        amount=-prices[user_vpn.id],
                        status=TransactionStatusChoices.SUCCESS,
                        source=TransactionSourceChoices.EVERYDAY_SYSTEM,
                        charge_date=charge_date,
                    )
            except Exception:
                # Подписки могло уже не быть или база могла отказать на этой строке. Баланс
                # не уменьшаем и подписку не отключаем: деньги есть, повторит следующий прогон.
                logger.exception('Daily charge failed for subscription %s', user_vpn.id)
                continue

            balance -= prices[user_vpn.id]
            charged += 1

        disabled_ids = {user_vpn.id for user_vpn in to_disable}
        remaining_daily_cost = sum(price for user_vpn_id, price in prices.items() if user_vpn_id not in disabled_ids)

    # Одно сообщение на аккаунт за прогон, по итоговому состоянию: иначе аккаунт с тремя
    # подписками получал подряд два предупреждения и отключение, противоречащие друг другу.
    if to_disable:
        message = OUT_OF_MONEY_TEXT
    elif charged and balance < remaining_daily_cost:
        message = LOW_BALANCE_TEXT
    else:
        # Прогон, ничего не изменивший (все уже списано сегодня), молчит.
        message = None

    return to_disable, message


@shared_task
def update_user_vpn():
    # Создаем бота
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)

    charge_date = _charge_date(timezone.now())

    subscriptions_by_user = defaultdict(list)
    for user_vpn in (
        UserVPN.objects.with_related_user()
        .with_related_server()
        .filter_by_enabled(True)
        .order_by('user_id', 'created_at', 'id')
    ):
        subscriptions_by_user[user_vpn.user_id].append(user_vpn)

    failed_disables = 0

    for user_id, subscriptions in subscriptions_by_user.items():
        try:
            to_disable, message = _charge_user(user_id, subscriptions, charge_date)
        except Exception:
            logger.exception('Daily charge skipped for user %s', user_id)
            continue

        for user_vpn in to_disable:
            logger.info('Disabling subscription %s: balance below daily price', user_vpn.id)
            # Отток пишется по решению биллинга, а не по успеху панели: аккаунт
            # остался без денег независимо от того, доехало ли отключение.
            subscription_disabled_no_funds(user_id, user_vpn.id, charge_date)
            try:
                asyncio.run(disable_vpn_user_from_server(user_vpn))
            except Exception:
                # Подписка осталась включенной и неоплаченной: панель недоступна. Списания
                # не было, поэтому следующий прогон повторит отключение. Молчать при этом
                # нельзя — клиент должен узнать, что деньги кончились.
                failed_disables += 1
                logger.exception(
                    'Disabling subscription %s failed, access stays open until the next run',
                    user_vpn.id,
                )

        if message:
            _notify(bot, subscriptions[0].user.telegram_id, message)

    if failed_disables:
        # Одна строка, по которой дежурный видит, что панель оставила доступ бесплатным.
        logger.error('Daily billing could not disable %s subscriptions, access left open', failed_disables)


@shared_task
def sync_expiry_times():
    """Mirror remaining balance days into 3x-ui client expiryTime so subscription
    clients (happ) display how many days are left. Does not create transactions and
    does not enable disabled clients."""
    from django.core.management import call_command
    call_command('sync_expiry_times')
