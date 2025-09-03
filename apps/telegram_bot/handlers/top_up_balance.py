from django.conf import settings
from telegram import Update, LabeledPrice
from telegram.ext import ContextTypes

from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.payments.constants import PROMO_AMOUNT
from apps.payments.models import Transaction
from apps.servers.models import TariffServer
from apps.telegram_bot.handlers.balance import show_balance
from apps.users.models import TelegramUser
from apps.telegram_bot.utils import get_user


async def top_up_balance_promo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user: TelegramUser = await get_user(update)

    if not (
            await Transaction.objects.filter_by_user(
                user_id=user.id,
            ).filter_by_source(
                source=TransactionSourceChoices.PROMO,
            ).aexists()
    ):
        await Transaction.objects.acreate(
            user=user,
            source=TransactionSourceChoices.PROMO,
            amount=PROMO_AMOUNT,
            status=TransactionStatusChoices.SUCCESS,
        )

        await update.callback_query.answer(
            text=f"Как новому пользователю - вам начислено {int(PROMO_AMOUNT)} рублей",
        )

        await show_balance(update, context)

        return


async def top_up_balance_one_month(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user: TelegramUser = await get_user(update)

    #todo: переделать потом
    tariff: TariffServer = await TariffServer.objects.aget()

    amount: int = int(tariff.price * 30 * 100)

    prices = [LabeledPrice("Цена", amount)]

    await context.bot.send_invoice(
        chat_id= update.effective_chat.id,
        title=f"Пополнить на {tariff.price * 30} руб.",
        description=f"Пополнить на {tariff.price * 30} руб.",
        payload='one_month',
        provider_token=settings.YOUMONEY_TOKEN,
        currency="RUB",
        prices=prices,
    )


async def pre_checkout_callback(update: Update, context):
    query = update.pre_checkout_query
    await query.answer(ok=True)


async def successful_payment_callback(update: Update, context):
    user: TelegramUser = await get_user(update)

    payment = update.message.successful_payment

    await Transaction.objects.acreate(
        user=user,
        source=TransactionSourceChoices.YOUMONEY,
        amount=round(payment.total_amount / 100, 2),
        status=TransactionStatusChoices.SUCCESS,
    )

    await show_balance(update, context)
