from django.db import models


class TransactionStatusChoices(models.TextChoices):
    PENDING = 'PENDING', 'Ожидание'
    SUCCESS = 'SUCCESS', 'Успешно'
    FAILED = 'FAILED', 'Неуспешно'


class TransactionSourceChoices(models.TextChoices):
    YOUMONEY = 'YOUMONEY', 'Юмани'
    PROMO = 'PROMO', 'Промо-баланс'
    EVERYDAY_SYSTEM = 'EVERYDAY_SYSTEM', 'Ежедневное списание'
    BUY = 'BUY', 'Покупка'
    MANUAL = 'MANUAL', 'Руками проставили'
    REFERRAL = 'REFERRAL', 'Реферальная система'
    COMPENSATION = 'COMPENSATION', 'Компенсация простоя'
