import logging
from decimal import Decimal

from asgiref.sync import sync_to_async
from django.conf import settings
from telegram import LabeledPrice, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from apps.analytics.funnel import (
    invoice_sent,
    payment_completed,
    pre_checkout_approved,
    promo_claimed,
    topup_plan_chosen,
)
from apps.analytics.recording import record_topup
from apps.payments.bonus import topup_bonus_amount
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
    await sync_to_async(promo_claimed)(user.id)

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
    user: TelegramUser = await get_user(update)

    # Выбор срока записывается раньше проверки провайдера, а не рядом со счётом:
    # при пустом токене воронка обязана обрываться именно здесь, и обрыв виден,
    # только если предыдущий шаг записан. Нажатие приходит с ранее разосланного
    # экрана, где кнопки сумм ещё были. Тариф для шага читается мягко: экран
    # «пополнение недоступно» до сих пор обходился без строки тарифа вообще, и
    # ронять его ради аналитики нельзя.
    chosen_tariff: TariffServer | None = await TariffServer.objects.afirst()
    if chosen_tariff is not None:
        await sync_to_async(topup_plan_chosen)(
            user.id, amount=chosen_tariff.price * count_days, days=count_days)

    # Кнопок сумм без провайдера уже нет, но нажатие может прийти со старого
    # экрана. Экран перерисовывается — на нём и написано, почему кнопок нет.
    if not payments_enabled():
        text, keyboard = await build_balance_screen(user)
        await render_screen(update, context, text, keyboard, toast=UNAVAILABLE_TOAST)
        return

    tariff: TariffServer = await TariffServer.objects.aget()
    plan_amount = tariff.price * count_days

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
        text, keyboard = await build_balance_screen(user, notice=PROVIDER_FAILED_NOTICE)
        await render_screen(update, context, text, keyboard, toast=UNAVAILABLE_TOAST)
        return

    await sync_to_async(invoice_sent)(user.id, amount=plan_amount, days=count_days)

    # Счёт приходит отдельным сообщением, экран не меняется — «часики» на
    # кнопке снимаются здесь.
    await answer_query(update)


async def pre_checkout_callback(update: Update, context):
    query = update.pre_checkout_query
    await query.answer(ok=True)
    await sync_to_async(_record_pre_checkout)(query.from_user.id, _rubles(query.total_amount))


def _record_pre_checkout(telegram_id: int, amount: Decimal) -> None:
    """Шаг воронки для pre-checkout, где обычный ``get_user`` неприменим.

    У этого апдейта нет ни `callback_query`, ни `message`, поэтому пользователь
    ищется по telegram_id. Платёж к этому моменту уже подтверждён, и ни поиск,
    ни запись не имеют права его отменить — отсюда собственный перехват вокруг
    запроса, которого нет внутри ``record_funnel_event``.
    """
    try:
        user_id = TelegramUser.objects.filter(telegram_id=telegram_id).values_list('id', flat=True).first()
    except Exception:
        logger.exception('analytics pre-checkout lookup for telegram user %s failed', telegram_id)
        return
    if user_id is None:
        return
    pre_checkout_approved(user_id, amount=amount)


def _rubles(kopecks: int) -> Decimal:
    """Сумма платежа приходит в копейках; воронка и отчёт считают в рублях."""
    return Decimal(kopecks) / 100


async def successful_payment_callback(update: Update, context):
    user: TelegramUser = await get_user(update)

    payment = update.message.successful_payment

    # Лестница объёмного бонуса общая с криптооплатой: за одни и те же деньги
    # клиент получает один и тот же баланс, каким бы способом он ни заплатил.
    amount = topup_bonus_amount(_rubles(payment.total_amount))

    topup = await Transaction.objects.acreate(
        user=user,
        source=TransactionSourceChoices.YOUMONEY,
        amount=amount,
        status=TransactionStatusChoices.SUCCESS,
    )
    # Сигнал уже записал событие с суммой, выведенной из лестницы бонусов выше.
    # Здесь известна сумма, реально снятая с карты, и она уточняет оценку.
    cash_amount = _rubles(payment.total_amount)
    await sync_to_async(record_topup)(topup, cash_amount=cash_amount)
    # Идентификатор платежа только хешируется в ключ идемпотентности. Пустым он
    # не приходит, но `None` уронил бы хеширование до перехвата внутри записи —
    # то есть аналитика уронила бы уже прошедший платёж.
    await sync_to_async(payment_completed)(
        user.id, amount=cash_amount, charge_id=str(payment.telegram_payment_charge_id or ''),
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
