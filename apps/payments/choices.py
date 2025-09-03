from django.db import models


class TransactionStatusChoices(models.TextChoices):
    PENDING = 'PENDING', 'Ожидание'
    SUCCESS = 'SUCCESS', 'Успешно'
    FAILED = 'FAILED', 'Неуспешно'


class TransactionSourceChoices(models.TextChoices):
    YOUMONEY = 'YOUMONEY', 'Юмани'
    PROMO = 'PROMO', 'Промо-баланс'
