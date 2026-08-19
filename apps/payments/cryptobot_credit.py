"""Зачисление оплаченного счёта CryptoBot — ровно один раз, откуда бы ни узнали.

Об оплате можно узнать двумя путями: опросом провайдера (`poll_cryptobot_invoices`)
и вебхуком. Пути независимы и могут сработать оба на один счёт, поэтому решение
«зачислять или нет» принимает не вызывающий, а атомарный переход ``paid``
False→True здесь: строку получает ровно один участник гонки, остальные видят
ноль обновлённых строк и молча выходят.

Деньги считает та же лестница, что и карта (`topup_bonus_amount`), и реферальный
процент начисляется по тем же правилам: способ оплаты не должен менять сумму,
которую клиент видит на балансе.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction as db_transaction

from apps.analytics.funnel import payment_completed
from apps.analytics.recording import record_topup
from apps.payments.bonus import topup_bonus_amount
from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.payments.models import CryptoBotInvoice, Transaction


logger = logging.getLogger(__name__)


def credit_cryptobot_invoice(invoice: CryptoBotInvoice) -> bool:
    """Зачислить счёт, если его ещё не зачисляли. True — зачислили сейчас.

    Ничего не бросает наружу в норме: аналитика гасит собственные ошибки, а сам
    денежный путь либо проходит целиком, либо не начинается — заявка на счёт
    остаётся неоплаченной и следующий опрос попробует снова.
    """
    claimed = CryptoBotInvoice.objects.filter(id=invoice.id, paid=False).update(paid=True)
    if not claimed:
        return False

    paid_rub = invoice.amount_rub
    amount = topup_bonus_amount(paid_rub)

    with db_transaction.atomic():
        topup = Transaction.objects.create(
            user_id=invoice.user_id,
            amount=amount,
            status=TransactionStatusChoices.SUCCESS,
            source=TransactionSourceChoices.CRYPTO,
        )
        referral_user_id = getattr(invoice.user, 'referral_user_id', None)
        if referral_user_id:
            Transaction.objects.create(
                user_id=referral_user_id,
                source=TransactionSourceChoices.REFERRAL,
                amount=int(amount / 100 * settings.REFERRAL_PERCENT),
                status=TransactionStatusChoices.SUCCESS,
                from_referral_user_id=invoice.user_id,
            )

    # Уплаченное, а не зачисленное: событие измеряет пришедшие деньги, а бонус
    # деньгами не является. Ключ идемпотентности — номер счёта у провайдера,
    # поэтому повторная запись невозможна, даже если событие уже было.
    record_topup(topup, cash_amount=paid_rub)
    payment_completed(invoice.user_id, amount=paid_rub, charge_id=f'cryptobot:{invoice.invoice_id}')

    logger.info(
        'cryptobot credited invoice_id=%s user_id=%s paid_rub=%s credited=%s',
        invoice.invoice_id, invoice.user_id, str(paid_rub), str(amount),
    )
    return True
