"""Экономический смысл каждой денежной строки — в коде, а не в описании.

``Transaction`` остаётся единственным источником правды для баланса: этот модуль
ничего в нём не меняет и не фильтрует, он только отвечает на вопрос «чем эта
строка является экономически». Отчёты обязаны спрашивать здесь, поэтому новый
источник не может тихо разойтись с моделью денег: незнакомый ``source``
классифицируется как ``UNKNOWN`` и виден в отчёте отдельной строкой.

Класс определяется парой (источник, знак суммы), а не одним источником:
``MANUAL`` с плюсом — выданный баланс, с минусом — корректировка, и точно так же
положительное ``EVERYDAY_SYSTEM`` может быть только возвратом списания.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Callable, Final

from apps.analytics.choices import CashBasisChoices, EconomicClassChoices, MoneyEventKindChoices
from apps.payments.choices import TransactionSourceChoices


ZERO: Final[Decimal] = Decimal('0.00')
CENT: Final[Decimal] = Decimal('0.01')

# Лестница объёмных бонусов из ``successful_payment_callback``: к сумме платежа
# добавляется процент, и на баланс попадает уже увеличенная сумма. Поэтому
# ``Transaction.amount`` пополнения — это не полученные деньги, а деньги плюс
# маркетинговая скидка, и разделить их обязано именно это место.
TOPUP_BONUS_LADDER: Final[tuple[tuple[Decimal, Decimal], ...]] = (
    (Decimal('2520'), Decimal('1.30')),
    (Decimal('1250'), Decimal('1.20')),
    (Decimal('600'), Decimal('1.10')),
    (Decimal('400'), Decimal('1.05')),
)


@dataclass(frozen=True)
class Classification:
    """Разложение одной строки на деньги, выручку, выданный баланс и выплату.

    Слагаемые не пересекаются и предназначены для суммирования по периоду.
    ``balance_delta`` всегда равна сумме транзакции: он существует для сверки с
    балансом, а не вместо него.
    """

    economic_class: str
    kind: str
    cash_basis: str
    balance_delta: Decimal
    cash_amount: Decimal = ZERO
    revenue_amount: Decimal = ZERO
    credit_amount: Decimal = ZERO
    payout_amount: Decimal = ZERO


def _decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _floor(value: Decimal) -> Decimal:
    return value.to_integral_value(rounding=ROUND_FLOOR)


def _ladder_bands() -> tuple[tuple[Decimal, Decimal | None, Decimal], ...]:
    """Диапазоны *начисленных* сумм по ступеням: (низ, верх, множитель).

    Множитель применялся с отбрасыванием дробной части, поэтому границе платежа
    соответствует границa начисления, а не наоборот. Верх ступени задаётся
    порогом следующей: у самой верхней его нет.
    """
    bands: list[tuple[Decimal, Decimal | None, Decimal]] = []
    cash_ceiling: Decimal | None = None
    for threshold, multiplier in TOPUP_BONUS_LADDER:
        low = _floor((threshold + CENT) * multiplier)
        high = None if cash_ceiling is None else _floor(cash_ceiling * multiplier)
        bands.append((low, high, multiplier))
        cash_ceiling = threshold
    return tuple(bands)


# ``[3276;∞)``, ``[1500;3024]``, ``[660;1375]``, ``[420;630]`` — и ``(0;400]``
# без бонуса. Диапазоны не пересекаются, поэтому ступень восстанавливается по
# начисленной сумме однозначно.
LADDER_BANDS: Final[tuple[tuple[Decimal, Decimal | None, Decimal], ...]] = _ladder_bands()
NO_BONUS_CEILING: Final[Decimal] = TOPUP_BONUS_LADDER[-1][0]


def split_topup(credited: Decimal) -> tuple[Decimal, Decimal, str]:
    """Разделить начисленное пополнение на полученные деньги и объёмный бонус.

    Возвращает ``(деньги, бонус, основание)``. Точность — до рубля: обратный ход
    через отброшенную дробную часть даёт нижнюю границу интервала, в котором
    лежит настоящий платёж, и ошибка одной строки меньше рубля.

    Начисление, не попадающее ни в один диапазон, ступенями не объясняется —
    например, если в тот период ступени были другими. Такая строка получает
    ``UNKNOWN``: деньги по ней пришли, но какая их часть была бонусом, история не
    хранит, и вся сумма считается верхней оценкой полученного.
    """
    if credited <= ZERO:
        return ZERO, ZERO, CashBasisChoices.UNKNOWN

    if credited <= NO_BONUS_CEILING:
        return credited, ZERO, CashBasisChoices.DERIVED

    for low, high, multiplier in LADDER_BANDS:
        if credited < low or (high is not None and credited > high):
            continue
        cash = (credited / multiplier).quantize(CENT, rounding=ROUND_CEILING)
        return cash, credited - cash, CashBasisChoices.DERIVED

    return credited, ZERO, CashBasisChoices.UNKNOWN


def classify(source: str, amount, *, measured_cash: Decimal | None = None) -> Classification:
    """Классифицировать одну денежную строку.

    ``measured_cash`` передаёт место вызова, которое знает настоящую сумму
    платежа. Без него сумма пополнения восстанавливается из лестницы бонусов и
    помечается как выведенная.
    """
    amount = _decimal(amount)

    if amount == ZERO:
        return Classification(
            economic_class=EconomicClassChoices.ADJUSTMENT,
            kind=MoneyEventKindChoices.NO_OP,
            cash_basis=CashBasisChoices.NONE,
            balance_delta=amount,
        )

    handler = _HANDLERS.get(source)
    if handler is None:
        return Classification(
            economic_class=EconomicClassChoices.UNKNOWN,
            kind=MoneyEventKindChoices.UNCLASSIFIED,
            cash_basis=CashBasisChoices.UNKNOWN,
            balance_delta=amount,
        )
    return handler(amount, measured_cash)


def _classify_topup(amount: Decimal, measured_cash: Decimal | None) -> Classification:
    if amount < ZERO:
        return _reversal(amount)
    if measured_cash is not None:
        cash = _decimal(measured_cash)
        return Classification(
            economic_class=EconomicClassChoices.CASH_IN,
            kind=MoneyEventKindChoices.TOPUP,
            cash_basis=CashBasisChoices.MEASURED,
            balance_delta=amount,
            cash_amount=cash,
            credit_amount=max(amount - cash, ZERO),
        )
    cash, bonus, basis = split_topup(amount)
    return Classification(
        economic_class=EconomicClassChoices.CASH_IN,
        kind=MoneyEventKindChoices.TOPUP,
        cash_basis=basis,
        balance_delta=amount,
        cash_amount=cash,
        credit_amount=bonus,
    )


def _classify_promo(amount: Decimal, measured_cash: Decimal | None) -> Classification:
    if amount < ZERO:
        return _reversal(amount)
    return Classification(
        economic_class=EconomicClassChoices.CREDIT_GRANTED,
        kind=MoneyEventKindChoices.SIGNUP_PROMO,
        cash_basis=CashBasisChoices.NONE,
        balance_delta=amount,
        credit_amount=amount,
    )


def _classify_compensation(amount: Decimal, measured_cash: Decimal | None) -> Classification:
    if amount < ZERO:
        return _reversal(amount)
    return Classification(
        economic_class=EconomicClassChoices.CREDIT_GRANTED,
        kind=MoneyEventKindChoices.OUTAGE_COMPENSATION,
        cash_basis=CashBasisChoices.NONE,
        balance_delta=amount,
        credit_amount=amount,
    )


def _classify_manual(amount: Decimal, measured_cash: Decimal | None) -> Classification:
    if amount < ZERO:
        return Classification(
            economic_class=EconomicClassChoices.ADJUSTMENT,
            kind=MoneyEventKindChoices.MANUAL_ADJUSTMENT,
            cash_basis=CashBasisChoices.NONE,
            balance_delta=amount,
        )
    # Единственный по-настоящему двусмысленный случай в этих данных: строка
    # означает и подарок, и деньги, принятые мимо провайдера. Различить их
    # существующие поля не позволяют, поэтому сумма попадает в выданный баланс,
    # а основание остаётся UNKNOWN и печатается отдельной строкой отчёта.
    return Classification(
        economic_class=EconomicClassChoices.CREDIT_GRANTED,
        kind=MoneyEventKindChoices.MANUAL_CREDIT,
        cash_basis=CashBasisChoices.UNKNOWN,
        balance_delta=amount,
        credit_amount=amount,
    )


def _classify_referral(amount: Decimal, measured_cash: Decimal | None) -> Classification:
    if amount < ZERO:
        return _reversal(amount)
    return Classification(
        economic_class=EconomicClassChoices.PAYOUT,
        kind=MoneyEventKindChoices.REFERRAL_PAYOUT,
        cash_basis=CashBasisChoices.NONE,
        balance_delta=amount,
        payout_amount=amount,
    )


def _classify_daily_charge(amount: Decimal, measured_cash: Decimal | None) -> Classification:
    if amount > ZERO:
        return _reversal(amount)
    return Classification(
        economic_class=EconomicClassChoices.REVENUE,
        kind=MoneyEventKindChoices.DAILY_CHARGE,
        cash_basis=CashBasisChoices.NONE,
        balance_delta=amount,
        revenue_amount=-amount,
    )


def _classify_purchase(amount: Decimal, measured_cash: Decimal | None) -> Classification:
    if amount > ZERO:
        return _reversal(amount)
    return Classification(
        economic_class=EconomicClassChoices.REVENUE,
        kind=MoneyEventKindChoices.SUBSCRIPTION_PURCHASE,
        cash_basis=CashBasisChoices.NONE,
        balance_delta=amount,
        revenue_amount=-amount,
    )


def _reversal(amount: Decimal) -> Classification:
    """Строка с неожиданным знаком: возврат или отмена, но не новая выручка."""
    return Classification(
        economic_class=EconomicClassChoices.ADJUSTMENT,
        kind=MoneyEventKindChoices.REVERSAL,
        cash_basis=CashBasisChoices.NONE,
        balance_delta=amount,
    )


_HANDLERS: Final[dict[str, Callable[[Decimal, Decimal | None], Classification]]] = {
    TransactionSourceChoices.YOUMONEY: _classify_topup,
    TransactionSourceChoices.PROMO: _classify_promo,
    TransactionSourceChoices.COMPENSATION: _classify_compensation,
    TransactionSourceChoices.MANUAL: _classify_manual,
    TransactionSourceChoices.REFERRAL: _classify_referral,
    TransactionSourceChoices.EVERYDAY_SYSTEM: _classify_daily_charge,
    TransactionSourceChoices.BUY: _classify_purchase,
}


def known_sources() -> tuple[str, ...]:
    """Источники, которым таксономия даёт экономический смысл."""
    return tuple(_HANDLERS)
