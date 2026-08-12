"""Разделение баланса на реальные деньги и подаренные — производная, а не второе хранилище.

Владелец не может отличить заплатившего клиента от начисленного руками: баланс —
одна знаковая сумма по ``Transaction``. Здесь она раскладывается на два счёта, но
**новых полей с деньгами не появляется**: оба счёта каждый раз пересчитываются по
тому же журналу, поэтому разойтись с балансом им негде. Их сумма равна
``annotate_balance()`` для любого аккаунта и на любой момент — это инвариант, а не
намерение, и он проверяется тестами и строкой ``mismatched_accounts`` в отчёте.

**Что чем является** решает ``apps.analytics.taxonomy`` — то же место, что и для
``money_report``. Своей классификации здесь нет, иначе она разошлась бы с отчётом
при первой же правке таксономии. Пополнение через провайдера разбирается ровно
так же, как в отчёте: платёж попадает на реальный счёт, а объёмный бонус лестницы
(5–30% сверх 1250 ₽ и ниже) — на бонусный, из одной и той же строки ``YOUMONEY``.

**Правило списания: бонус тратится первым.** Причина не бухгалтерская, а
продуктовая: подарок должен сгорать раньше денег, за которые человек заплатил,
иначе «бонус» на экране обещает то, до чего очередь не дойдёт никогда. У аккаунта
без единого начисления бонусный счёт всегда нулевой, а реальный побайтово равен
сегодняшнему балансу — поведение таких аккаунтов не меняется ничем.

Бонусный счёт хранится одним числом, а не набором начислений: пока у начислений
нет срока жизни, порядок внутри бонуса ненаблюдаем — «сначала самое старое» и
любой другой порядок дают те же два числа. Появится срок годности — здесь
появятся лоты, и правило уже названо.

**Чего история не знает.** ``MANUAL`` с плюсом (191 386 ₽ на 2026-08-12) означает
только то, что владелец вписал баланс руками; заплатил ли человек мимо провайдера,
не хранится нигде. Такие суммы целиком идут в бонус — уверенное «это реальные
деньги» было бы самой крупной ошибкой модели, — и это ровно та же трактовка, что
у ``cash_basis=UNKNOWN`` в отчёте. Обратное тоже верно: часть «бонуса» отдельных
аккаунтов может быть настоящими деньгами, и различить их нельзя.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Iterator, Sequence

from django.db.models import Sum
from django.utils import timezone

from apps.analytics.choices import EconomicClassChoices
from apps.analytics.taxonomy import classify
from apps.payments.models import Transaction


ZERO = Decimal('0.00')


@dataclass(frozen=True)
class BalanceSplit:
    """Два счёта одного баланса. ``total`` — то самое число, что видит пользователь.

    ``total`` намеренно вычисляется, а не хранится: поле можно было бы записать
    отдельно от слагаемых и получить третью версию баланса.
    """

    real: Decimal = ZERO
    bonus: Decimal = ZERO
    # Приток из источника, которого таксономия не знает. Отнесён к реальным
    # деньгам (см. ``_replay``) и виден отдельным числом, чтобы новый способ
    # оплаты проявился как пробел, а не растворился в подарках.
    unclassified: Decimal = ZERO

    @property
    def total(self) -> Decimal:
        return self.real + self.bonus


def split_balance(user_id: int, *, as_of=None) -> BalanceSplit:
    """Разложить баланс одного аккаунта на момент ``as_of`` (по умолчанию — сейчас)."""
    return _replay((source, amount) for _user_id, source, amount in _rows([user_id], as_of))


def split_balances(user_ids: Sequence[int] | None = None, *, as_of=None) -> dict[int, BalanceSplit]:
    """Разложить балансы многих аккаунтов одним проходом по журналу.

    ``user_ids=None`` — все аккаунты. Аккаунта без транзакций в ответе нет: его
    разложение — нули, ровно как ``Coalesce`` в ``annotate_balance()``.
    """
    return {user_id: _replay(rows) for user_id, rows in _grouped(_rows(user_ids, as_of))}


def attach_balance_split(users: Iterable, *, as_of=None) -> list:
    """Проставить ``balance_split`` объектам пользователей и вернуть их списком.

    Настоящей SQL-аннотацией это быть не может: распределение списаний
    последовательное, а смысл строки задаёт таксономия на Python. Оконная функция
    повторила бы таксономию в SQL — то есть завела бы второй источник правды,
    который и должен был исчезнуть.
    """
    materialised = list(users)
    splits = split_balances([user.id for user in materialised], as_of=as_of)
    for user in materialised:
        user.balance_split = splits.get(user.id, BalanceSplit())
    return materialised


def aggregate_split(*, as_of=None) -> dict:
    """Свод по всем аккаунтам плюс сверка суммы счетов с журналом.

    ``mismatched_accounts`` сравнивает разложение с той же агрегатной суммой, что
    считает ``annotate_balance()`` (без фильтра по статусу — он там тоже
    отсутствует). Ноль здесь — это утверждение, проверенное на живых данных, а не
    обещание из документации.
    """
    boundary = _boundary(as_of)
    splits = split_balances(as_of=boundary)
    ledger = {
        row['user_id']: Decimal(row['total'] or 0)
        for row in _base_queryset(None, boundary).values('user_id').annotate(total=Sum('amount'))
    }

    real_total = sum((split.real for split in splits.values()), ZERO)
    bonus_total = sum((split.bonus for split in splits.values()), ZERO)
    overdrawn = [split for split in splits.values() if split.total < ZERO]
    mismatched = sum(1 for user_id, split in splits.items() if split.total != ledger.get(user_id, ZERO))

    return {
        'as_of': boundary.isoformat() if boundary is not None else None,
        'accounts': len(splits),
        # Из SQL, а не из разложения: рядом с ``real_total + bonus_total`` это
        # сверка двух независимых путей, а не то же число под другим именем.
        'ledger_total': sum(ledger.values(), ZERO),
        'real_total': real_total,
        'bonus_total': bonus_total,
        'accounts_with_bonus': sum(1 for split in splits.values() if split.bonus > ZERO),
        'accounts_overdrawn': len(overdrawn),
        'overdraft_total': sum((split.total for split in overdrawn), ZERO),
        'unclassified_total': sum((split.unclassified for split in splits.values()), ZERO),
        'mismatched_accounts': mismatched,
    }


def _replay(rows: Iterable[tuple[str, Decimal]]) -> BalanceSplit:
    """Прокрутить строки аккаунта по порядку, распределяя приток и списания.

    Сумма счетов после каждой строки меняется ровно на сумму строки, поэтому
    равенство балансу держится не проверкой в конце, а конструкцией.
    """
    real = bonus = unclassified = ZERO
    # Сколько бонуса уже потрачено: возврат списания обязан вернуться туда,
    # откуда деньги брали, иначе обратная строка тихо превращает подарок в деньги.
    bonus_spent = ZERO

    for source, amount in rows:
        line = classify(source, amount)
        delta = line.balance_delta

        if delta > ZERO:
            granted = line.credit_amount + line.payout_amount
            bonus += granted
            own = delta - granted
            if own > ZERO and line.economic_class == EconomicClassChoices.ADJUSTMENT:
                returned = min(own, bonus_spent)
                bonus += returned
                bonus_spent -= returned
                own -= returned
            if own > ZERO and line.economic_class == EconomicClassChoices.UNKNOWN:
                unclassified += own
            real += own
        elif delta < ZERO:
            debit = -delta
            from_bonus = min(bonus, debit)
            bonus -= from_bonus
            bonus_spent += from_bonus
            real -= debit - from_bonus

        if real < ZERO < bonus:
            # Минус на реальном счёте не сосуществует с бонусом: долг закрывает
            # любой следующий приток. Иначе экран обещал бы «у вас 50 ₽ бонусов»
            # аккаунту, общий баланс которого отрицательный.
            covered = min(bonus, -real)
            real += covered
            bonus -= covered
            bonus_spent += covered

    return BalanceSplit(real=real, bonus=bonus, unclassified=unclassified)


def _base_queryset(user_ids: Sequence[int] | None, boundary):
    """Строки журнала без фильтра по статусу — как в ``annotate_balance()``.

    Фильтр по ``SUCCESS`` здесь был бы расхождением с балансом: он суммирует
    ``PENDING`` и ``FAILED`` тоже, и разложение обязано считать ровно то же.
    """
    queryset = Transaction.objects.all()
    if user_ids is not None:
        queryset = queryset.filter(user_id__in=user_ids)
    if boundary is not None:
        queryset = queryset.filter(created_at__lte=boundary)
    return queryset


def _rows(user_ids: Sequence[int] | None, as_of) -> Iterator[tuple[int, str, Decimal]]:
    queryset = _base_queryset(user_ids, _boundary(as_of))
    return queryset.order_by('user_id', 'created_at', 'id').values_list('user_id', 'source', 'amount').iterator()


def _grouped(rows: Iterable[tuple[int, str, Decimal]]) -> Iterator[tuple[int, list[tuple[str, Decimal]]]]:
    current: int | None = None
    batch: list[tuple[str, Decimal]] = []
    for user_id, source, amount in rows:
        if user_id != current:
            if current is not None:
                yield current, batch
            current, batch = user_id, []
        batch.append((source, amount))
    if current is not None:
        yield current, batch


def _boundary(as_of):
    """Граница «на момент». Дата означает конец этих суток, а не их начало."""
    if as_of is None:
        return None
    if not isinstance(as_of, datetime.datetime):
        as_of = datetime.datetime.combine(as_of, datetime.time.max)
    if timezone.is_naive(as_of):
        as_of = as_of.replace(tzinfo=datetime.timezone.utc)
    return as_of
