from django.db import models


class EconomicClassChoices(models.TextChoices):
    """Пять экономических смыслов, к которым сводится любая денежная строка."""

    CASH_IN = 'CASH_IN', 'Пришли реальные деньги'
    REVENUE = 'REVENUE', 'Признанная выручка'
    CREDIT_GRANTED = 'CREDIT_GRANTED', 'Выдан баланс бесплатно'
    PAYOUT = 'PAYOUT', 'Выплата партнёру'
    ADJUSTMENT = 'ADJUSTMENT', 'Внутренняя корректировка'
    UNKNOWN = 'UNKNOWN', 'Не классифицировано'


class MoneyEventKindChoices(models.TextChoices):
    """Более узкий разрез внутри экономического класса, нужный отчёту."""

    TOPUP = 'TOPUP', 'Пополнение через платёжного провайдера'
    SIGNUP_PROMO = 'SIGNUP_PROMO', 'Промо-баланс новичка'
    OUTAGE_COMPENSATION = 'OUTAGE_COMPENSATION', 'Компенсация простоя'
    MANUAL_CREDIT = 'MANUAL_CREDIT', 'Начисление руками'
    MANUAL_ADJUSTMENT = 'MANUAL_ADJUSTMENT', 'Списание руками'
    REFERRAL_PAYOUT = 'REFERRAL_PAYOUT', 'Реферальная выплата'
    DAILY_CHARGE = 'DAILY_CHARGE', 'Ежедневное списание'
    SUBSCRIPTION_PURCHASE = 'SUBSCRIPTION_PURCHASE', 'Плата за новую подписку'
    REVERSAL = 'REVERSAL', 'Обратная строка к списанию или начислению'
    NO_OP = 'NO_OP', 'Нулевая строка'
    UNCLASSIFIED = 'UNCLASSIFIED', 'Источник не известен таксономии'


class CashBasisChoices(models.TextChoices):
    """Насколько мы знаем, что за строкой стоят настоящие деньги.

    Разделение существует потому, что ``Transaction.amount`` хранит *начисленный
    баланс*, а не полученную сумму: у пополнений к сумме платежа добавляется
    объёмный бонус, а начисления руками вообще не говорят, платил ли кто-то.
    """

    MEASURED = 'MEASURED', 'Сумма платежа известна от места вызова'
    DERIVED = 'DERIVED', 'Восстановлена из начисленного по лестнице бонусов'
    NONE = 'NONE', 'Денег не было по определению'
    UNKNOWN = 'UNKNOWN', 'Деньги могли пройти мимо системы, сумма невосстановима'


class DateBasisChoices(models.TextChoices):
    """Откуда взята дата, к которой отчёт относит строку."""

    CHARGE_DATE = 'CHARGE_DATE', 'Ключ идемпотентности ежедневного списания'
    CREATED_AT = 'CREATED_AT', 'Момент создания строки'


class EventOriginChoices(models.TextChoices):
    LIVE = 'LIVE', 'Записано в момент события'
    BACKFILL = 'BACKFILL', 'Восстановлено из истории'


class FunnelStepChoices(models.TextChoices):
    """Шаги пути клиента: от экрана оплаты до отключения за неуплату."""

    BALANCE_SCREEN_SHOWN = 'BALANCE_SCREEN_SHOWN', 'Открыт экран пополнения'
    PROMO_CLAIMED = 'PROMO_CLAIMED', 'Забран промо-баланс'
    TOPUP_PLAN_CHOSEN = 'TOPUP_PLAN_CHOSEN', 'Выбран срок пополнения'
    INVOICE_SENT = 'INVOICE_SENT', 'Отправлен счёт'
    PRE_CHECKOUT_APPROVED = 'PRE_CHECKOUT_APPROVED', 'Пройден pre-checkout'
    PAYMENT_COMPLETED = 'PAYMENT_COMPLETED', 'Платёж завершён'
    SUBSCRIPTION_CREATED = 'SUBSCRIPTION_CREATED', 'Создана подписка'
    SUBSCRIPTION_REFUSED_NO_FUNDS = 'SUBSCRIPTION_REFUSED_NO_FUNDS', 'Отказ в подписке из-за баланса'
    SUBSCRIPTION_REMOVED = 'SUBSCRIPTION_REMOVED', 'Подписка удалена пользователем'
    SUBSCRIPTION_DISABLED_NO_FUNDS = 'SUBSCRIPTION_DISABLED_NO_FUNDS', 'Подписка отключена биллингом'
    # Восстанавливается из истории: у старых списаний нет ссылки на подписку,
    # поэтому пропуск в биллинге виден только на уровне аккаунта.
    ACCOUNT_BILLING_LAPSED = 'ACCOUNT_BILLING_LAPSED', 'Аккаунт перестал списываться'
    ACCOUNT_BILLING_RESUMED = 'ACCOUNT_BILLING_RESUMED', 'Аккаунт снова начал списываться'
