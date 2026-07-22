from decimal import Decimal

from asgiref.sync import async_to_sync
from django.conf import settings

from apps.payments.choices import (
    PaymentMethodChoices,
    ProductLineChoices,
    TransactionSourceChoices,
    TransactionStatusChoices,
)
from apps.payments.models import Invoice, Transaction
from apps.users.models import TelegramUser


def apply_top_up_bonus(amount: float | Decimal) -> int:
    amount = float(amount)
    if amount > 2520:
        return int(amount + amount * 0.3)
    if amount > 1250:
        return int(amount + amount * 0.2)
    if amount > 600:
        return int(amount + amount * 0.1)
    if amount > 400:
        return int(amount + amount * 0.05)
    return int(amount)


async def credit_top_up(
    user: TelegramUser,
    amount: float | Decimal,
    product_line: str,
    source: str,
    payment_method: str = '',
    invoice: Invoice | None = None,
    from_referral_user: TelegramUser | None = None,
) -> Transaction:
    user = await TelegramUser.objects.select_related('referral_user').aget(id=user.id)
    credited_amount = apply_top_up_bonus(amount)

    transaction = await Transaction.objects.acreate(
        user=user,
        source=source,
        amount=credited_amount,
        status=TransactionStatusChoices.SUCCESS,
        product_line=product_line,
        payment_method=payment_method or '',
        invoice=invoice,
        from_referral_user=from_referral_user,
    )

    if user.referral_user and source in (
        TransactionSourceChoices.YOUMONEY,
        TransactionSourceChoices.CRYPTOBOT,
    ):
        referral_amount = int(float(credited_amount) / 100 * settings.REFERRAL_PERCENT)
        await Transaction.objects.acreate(
            user=user.referral_user,
            source=TransactionSourceChoices.REFERRAL,
            amount=referral_amount,
            status=TransactionStatusChoices.SUCCESS,
            product_line=product_line,
            from_referral_user=user,
        )

    return transaction


def credit_top_up_sync(
    user: TelegramUser,
    amount: float | Decimal,
    product_line: str,
    source: str,
    payment_method: str = '',
    invoice: Invoice | None = None,
) -> Transaction:
    return async_to_sync(credit_top_up)(
        user=user,
        amount=amount,
        product_line=product_line,
        source=source,
        payment_method=payment_method,
        invoice=invoice,
    )


def build_payment_payload(
    user_id: int,
    product_line: str,
    count_days: int,
    percent: int = 0,
) -> dict:
    return {
        'user_id': user_id,
        'product_line': product_line,
        'count_days': count_days,
        'percent': percent,
    }
