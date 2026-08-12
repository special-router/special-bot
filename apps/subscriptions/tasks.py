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

from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.payments.models import Transaction
from apps.servers.models import TariffServer
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN
from apps.vpn.services.remove_vpn_user_from_server import disable_vpn_user_from_server


logger = logging.getLogger(__name__)

OUT_OF_MONEY_TEXT = 'Закончились деньги на балансе. Доступ к услугам остановлен'
LOW_BALANCE_TEXT = 'Пополните баланс, денег осталось на 1 день'


def _notify(bot: Bot, telegram_id: int, text: str):
    # todo: добавить вывод логов
    with contextlib.suppress(Exception):
        asyncio.run(bot.send_message(chat_id=telegram_id, text=text))
        # это чтобы слишком часто сообщения в телегу не отправлять
        time.sleep(1)


def _charge_user(user_id: int, subscriptions: list[UserVPN], charge_date: datetime.date):
    """Списать дневную плату по всем подпискам одного аккаунта.

    Баланс общий на аккаунт, поэтому он читается один раз и уменьшается по ходу цикла:
    решение по каждой подписке принимается по живому остатку, а не по значению до цикла.
    Подписки финансируются в порядке created_at, так что при нехватке денег отключаются
    самые новые. Списание никогда не уводит баланс в минус: если остатка не хватает,
    транзакция не создается вовсе.

    Возвращает решения (подписка, отключить, текст) для исполнения уже после коммита:
    сеть панели и телеграма не должна удерживать открытую денежную транзакцию.
    """
    outcomes = []

    with transaction.atomic():
        # Блокировка аккаунта сериализует параллельные прогоны биллинга по одному балансу.
        user = TelegramUser.objects.select_for_update().get(id=user_id)

        balance = TelegramUser.objects.filter(id=user_id).annotate_balance().values_list('balance', flat=True).first()
        already_charged = set(
            Transaction.objects.filter_by_source(TransactionSourceChoices.EVERYDAY_SYSTEM)
            .filter(charge_date=charge_date, user_vpn__in=subscriptions)
            .values_list('user_vpn_id', flat=True)
        )

        for user_vpn in subscriptions:
            if user_vpn.id in already_charged:
                continue

            tariff: TariffServer = user_vpn.server.tariff

            if balance < tariff.price:
                outcomes.append((user_vpn, True, OUT_OF_MONEY_TEXT))
                continue

            Transaction.objects.create(
                user=user,
                user_vpn=user_vpn,
                amount=-tariff.price,
                status=TransactionStatusChoices.SUCCESS,
                source=TransactionSourceChoices.EVERYDAY_SYSTEM,
                charge_date=charge_date,
            )
            balance -= tariff.price

            if balance < tariff.price * 2:
                outcomes.append((user_vpn, False, LOW_BALANCE_TEXT))

    return outcomes


@shared_task
def update_user_vpn():
    # Создаем бота
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)

    charge_date = timezone.now().astimezone(datetime.timezone.utc).date()

    subscriptions_by_user = defaultdict(list)
    for user_vpn in (
        UserVPN.objects.with_related_user()
        .with_related_server()
        .filter_by_enabled(True)
        .order_by('user_id', 'created_at', 'id')
    ):
        subscriptions_by_user[user_vpn.user_id].append(user_vpn)

    for user_id, subscriptions in subscriptions_by_user.items():
        try:
            outcomes = _charge_user(user_id, subscriptions, charge_date)
        except Exception:
            logger.exception('Daily charge skipped for user %s', user_id)
            continue

        for user_vpn, disable, text in outcomes:
            if disable:
                logger.info('Disabling subscription %s: balance below daily price', user_vpn.id)
                try:
                    asyncio.run(disable_vpn_user_from_server(user_vpn))
                except Exception:
                    # Подписка не списана, поэтому следующий прогон повторит отключение.
                    logger.exception('Disabling subscription %s failed', user_vpn.id)
                    continue

            _notify(bot, user_vpn.user.telegram_id, text)


@shared_task
def sync_expiry_times():
    """Mirror remaining balance days into 3x-ui client expiryTime so subscription
    clients (happ) display how many days are left. Does not create transactions and
    does not enable disabled clients."""
    from django.core.management import call_command
    call_command('sync_expiry_times')
