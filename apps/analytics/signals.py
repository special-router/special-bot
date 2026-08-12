"""Наблюдение за денежными строками без единой правки в денежном коде.

Строки ``Transaction`` создаются в шести местах — обработчиках бота, задаче
биллинга и команде компенсаций. Сигнал ловит их все сразу, поэтому места вызова
не обязаны знать об аналитике и не могут забыть её позвать. Работа сигнала
целиком уходит в ``on_commit`` и гасится в лог, так что денежный путь не
удлиняется на открытой транзакции и не может упасть из-за аналитики.

Чего сигнал не видит: изменения строки после создания (сейчас статус никто не
меняет) и суммы, реально списанной провайдером — её знает только обработчик
платежа, и до тех пор пока он не позовёт ``record_topup``, сумма пополнения
восстанавливается по лестнице бонусов и помечается как выведенная.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.analytics.recording import schedule_money_event
from apps.payments.models import Transaction


@receiver(post_save, sender=Transaction, dispatch_uid='analytics_money_event')
def _record_transaction(sender, instance: Transaction, created: bool, **kwargs) -> None:
    if not created:
        return
    schedule_money_event(instance)
