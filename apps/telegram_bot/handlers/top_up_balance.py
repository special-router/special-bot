import logging

from django.conf import settings
from telegram import LabeledPrice, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.payments.constants import PROMO_AMOUNT
from apps.payments.models import Transaction
from apps.servers.models import TariffServer
from apps.telegram_bot.handlers.balance import build_balance_screen, show_balance
from apps.telegram_bot.ui import answer_query, render_screen
from apps.telegram_bot.utils import get_user, payments_enabled
from apps.users.models import TelegramUser


logger = logging.getLogger(__name__)

UNAVAILABLE_TOAST = 'Пополнение временно недоступно.'
PROVIDER_FAILED_NOTICE = (
    'Счёт выставить не удалось: платёжный провайдер отклонил запрос. Попробуйте позже.'
)


async def top_up_balance_promo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user: TelegramUser = await get_user(update)

    if (
        await Transaction.objects.filter_by_user(
            user_id=user.id,
        )
        .filter_by_source(
            source=TransactionSourceChoices.PROMO,
        )
        .aexists()
    ):
        # Кнопка пропадает после начисления, но в разосланных ранее экранах
        # она осталась: нажатие обязано хотя бы объяснить, почему ничего нет.
        await answer_query(update, 'Бонус уже начислен.')
        return

    await Transaction.objects.acreate(
        user=user,
        source=TransactionSourceChoices.PROMO,
        amount=PROMO_AMOUNT,
        status=TransactionStatusChoices.SUCCESS,
    )

    # Пользователь перечитывается: начисление только что создано, и в
    # аннотированном ранее балансе его ещё нет.
    text, keyboard = await build_balance_screen(
        await get_user(update),
        notice=f'Как новому пользователю вам начислено {int(PROMO_AMOUNT)} руб.',
    )
    await render_screen(update, context, text, keyboard, toast=f'Начислено {int(PROMO_AMOUNT)} руб.')


async def top_up_balance_one_month(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await top_up_balance_days(update, context, 30)


async def top_up_balance_two_month(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await top_up_balance_days(update, context, 60, percent=5)


async def top_up_balance_three_month(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await top_up_balance_days(update, context, 90, percent=10)


async def top_up_balance_six_month(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await top_up_balance_days(update, context, 180, percent=20)


async def top_up_balance_year(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await top_up_balance_days(update, context, 365, percent=30)


async def top_up_balance_days(
    update: Update, context: ContextTypes.DEFAULT_TYPE, count_days: int, percent: int = 0
) -> None:
    # Кнопок сумм без провайдера уже нет, но нажатие может прийти со старого
    # экрана. Экран перерисовывается — на нём и написано, почему кнопок нет.
    if not payments_enabled():
        text, keyboard = await build_balance_screen(await get_user(update))
        await render_screen(update, context, text, keyboard, toast=UNAVAILABLE_TOAST)
        return

    tariff: TariffServer = await TariffServer.objects.aget()

    amount: int = int(tariff.price * count_days * 100)

    prices = [LabeledPrice('Цена', amount)]

    title: str = f"Пополнить на {tariff.price * count_days} руб."

    if percent > 0:
        title = f'{title} (+{percent}% к балансу)'

    # Отозванный или испорченный токен провайдер отклоняет здесь, и до этой
    # обёртки нажатие в таком случае не отвечало вообще ничего.
    try:
        await context.bot.send_invoice(
            chat_id=update.effective_chat.id,
            title=title,
            description=title,
            payload='one_month',
            provider_token=settings.YOUMONEY_TOKEN,
            currency='RUB',
            prices=prices,
        )
    except TelegramError:
        logger.exception('send_invoice rejected for %s days', count_days)
        text, keyboard = await build_balance_screen(await get_user(update), notice=PROVIDER_FAILED_NOTICE)
        await render_screen(update, context, text, keyboard, toast=UNAVAILABLE_TOAST)
        return

    # Счёт приходит отдельным сообщением, экран не меняется — «часики» на
    # кнопке снимаются здесь.
    await answer_query(update)


async def pre_checkout_callback(update: Update, context):
    query = update.pre_checkout_query
    await query.answer(ok=True)


async def successful_payment_callback(update: Update, context):
    user: TelegramUser = await get_user(update)

    payment = update.message.successful_payment

    amount = round(payment.total_amount / 100, 2)

    # todo: костыль
    if amount > 2520:
        amount = int(amount + amount * 0.3)
    elif amount > 1250:
        amount = int(amount + amount * 0.2)
    elif amount > 600:
        amount = int(amount + amount * 0.1)
    elif amount > 400:
        amount = int(amount + amount * 0.05)

    await Transaction.objects.acreate(
        user=user,
        source=TransactionSourceChoices.YOUMONEY,
        amount=amount,
        status=TransactionStatusChoices.SUCCESS,
    )

    if user.referral_user:
        await Transaction.objects.acreate(
            user=user.referral_user,
            source=TransactionSourceChoices.REFERRAL,
            amount=int(amount / 100 * settings.REFERRAL_PERCENT),
            status=TransactionStatusChoices.SUCCESS,
            from_referral_user=user,
        )

    await show_balance(update, context)
