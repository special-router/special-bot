from typing import Self

from django.db import models
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce


class TelegramUserQuerySet(models.QuerySet):
    def annotate_balance(self) -> Self:
        return self.annotate(
            balance=Coalesce(
                Sum('transactions__amount'),
                Value(0.0),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        )

    def filter_by_telegram_id(self, telegram_id: int) -> Self:
        return self.filter(telegram_id=telegram_id)
