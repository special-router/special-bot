from django.db import models
from typing import Self


class TransactionQuerySet(models.QuerySet):
    def filter_by_user(self, user_id: int) -> Self:
        return self.filter(user_id=user_id)

    def filter_by_status(self, status: str) -> Self:
        return self.filter(status=status)

    def filter_by_source(self, source: str) -> Self:
        return self.filter(source=source)
